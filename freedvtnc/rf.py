#!/usr/bin/env python3
import pyaudio
from enum import Enum
from typing import List
import logging
from threading import Lock
import time
import audioop
import random
import threading
import sys

# This deals with all the RF things and resampling

# Packet structure
#
#
# preamble (random bytes defined below base_preamble) * bytes_per_frame * preamble frame count - needed to get modem sync
# packet_length, 2 bytes, unsigned big
# data + padding to fit event even frame count
# postamble 0x01 * bytes_per_frame this isn't strictly needed but is suggested to allow the modem to finish decoding the last frame. between packets don't send this, just the preamble

class rx_state(Enum): # might make a proper state machine later
    SEARCH = 1
    SYNC =  2
    RECEIVE = 3
    RECOVER = 4
    PARITY = 5 # skips the next frame as it'll be used unused parity

def list_audio_devices() -> list:
    p = pyaudio.PyAudio()
    devices = []
    for x in range(0, p.get_device_count()):
        devices.append(f"{x} - {p.get_device_info_by_index(x)['name']}")
    return devices

class Rf():
    def __init__(self, 
                    modem,
                    callback,
                    rx_device="pulse",
                    tx_device=None,
                    audio_sample_rate=48000,
                    modem_sample_rate=8000,
                    max_packet_size=2047,
                    preamble_frame_count=5,
                    postamble_frame_count=4, # we need to send a little bit extra to receive the last frame
                    rig=None,
                    post_tx_wait_min=10,
                    post_tx_wait_max=10,
                    max_packets=3
                ):
        
        self.tx_buffer=[]
        self.tx_sample_state = None

        self.state = rx_state.SEARCH
        self.max_packet_size = max_packet_size
        self.max_packets = max_packets

        self.modem=modem
        self.rig=rig
        self.lock=Lock()
        self.tx_lock=Lock()
        self.rx_locked=False


                        # if the modem bytes per frame is larger than our preable we repeat, if not we truncate
        self.preamble = b'\x00' * modem.bytes_per_frame
        self.preamble_frame_count = preamble_frame_count
        self.postamble_frame_count = postamble_frame_count
        self.callback = callback
        self.post_tx_wait_min = post_tx_wait_min
        self.post_tx_wait_max = post_tx_wait_max

        self.audio_sample_rate = audio_sample_rate
        self.modem_sample_rate = modem_sample_rate
        self.sampele_state = None

        self.rx_frame_count = 0


        p = pyaudio.PyAudio()
        # Find audio interface from name
        try:
            rx_dev = int(rx_device)
        except:
            pass
        try:
            tx_dev = int(tx_device)
        except:
            pass        
        for x in range(0, p.get_device_count()):
            if p.get_device_info_by_index(x)["name"] == rx_device:
                rx_dev = x
            if p.get_device_info_by_index(x)["name"] == tx_device:
                tx_dev = x
        if rx_device == False:
            rx_dev = 0
        if tx_device == False:
            tx_dev = 0

        if rx_device == 'stdin':
            self.stream_rx = sys.stdin.buffer
        else:
            self.stream_rx = p.open(format=pyaudio.paInt16, 
                            channels=1,
                            rate=audio_sample_rate,
                            frames_per_buffer=modem.get_n_max_modem_samples(),
                            input=True,
                            input_device_index=rx_dev
                        )
        if tx_device == 'stdout':
            self.stream_tx = sys.stdout.buffer
        elif tx_device != None:
            self.stream_tx = p.open(format=pyaudio.paInt16, 
                            channels=1,
                            rate=audio_sample_rate,
                            frames_per_buffer=modem.get_n_max_modem_samples(),
                            output=True,
                            output_device_index=tx_dev
                        )


        self.tx_thread = self.TXThread(self.process_queue)
        self.tx_thread.setDaemon(True)
        self.tx_thread.start()
        logging.debug("Started TX Thread")


    def rx(self):
        if type(self.stream_rx) == pyaudio.Stream:
            audio_sample = self.stream_rx.read(int(self.modem.nin*(self.audio_sample_rate/self.modem_sample_rate)), exception_on_overflow = False) 
        else: # for stdin
            audio_sample = self.stream_rx.read((int(self.modem.nin*(self.audio_sample_rate/self.modem_sample_rate)))*2) # times 2 for 16bit samples 
        (audio_sample, self.sampele_state) = audioop.ratecv(audio_sample,2,1,self.audio_sample_rate, self.modem_sample_rate, self.sampele_state)

        frame = self.modem.demodulate(audio_sample, self.rx_frame_count)
        if audio_sample == len(audio_sample) * b'\x00': #don't demodulate silence as that breaks the freedv modem
            if self.rx_locked == True:
                self.rx_locked = False
                self.lock.release()       
            return
        
        
        # RX statemachine
        # If we are in search mode we are looking for preambles
        header = False

        if self.state == rx_state.SEARCH:
            if frame.data == self.preamble and frame.valid and frame.sync == True:
                self.state = rx_state.SYNC # the next frame could be a packet
                logging.debug("RX STATE -> SYNC: Preamble found, waiting for header")
                self.rx_frame_count = 0
        if self.state == rx_state.RECOVER:
            self.rx_frame_count = 0
            if (not frame.valid or frame.sync == False):
                logging.debug(f"Parity block not received - could not recover")
                self.state = rx_state.SEARCH
            else:
                self.rx_parity.add_block(frame.data)
                logging.debug(f"Received parity block trying to recover")
                logging.debug(f"Calculated Parity: {bytes(self.rx_parity.parity_block).hex()}")
                logging.debug(f"Packet before recovery: {self.packet_data.hex()}")
                logging.debug(f"Location of parity issue: {self.frame_error_location}")
                missing_frame = bytes(self.rx_parity.parity_block)
                packet = bytearray(self.packet_data) 
                packet[self.frame_error_location:self.frame_error_location + self.modem.bytes_per_frame] = missing_frame
                packet = bytes(packet)[:self.rx_length] # since for parity we use full frames we need to truncate this back down to the correct length
                self.packet_data = packet
                self.state = rx_state.SEARCH
                logging.debug("RX STATE -> SEARCH: Reached end of packet")
                logging.info(f"RXed Packet: {self.packet_data}")
                logging.info(f"RXed Packet HEX: {self.packet_data.hex()}")
                self.callback(self.packet_data)
        if (not frame.valid or frame.sync == False) and self.state != rx_state.SEARCH:
            if self.state == rx_state.RECEIVE and self.frame_errors == 0: # we might be able to recover using parity in this case we pad out the packet with blank bytes until we know what they are
                self.frame_errors += 1
                self.frame_error_location = len(self.packet_data)
                logging.debug(f"Dropped frame but might be able to cover - missing frame at {self.frame_error_location}")
                self.rx_frame_count += 1
                self.packet_data += b'\x00' * self.modem.bytes_per_frame
                self.remaining_bytes -= self.modem.bytes_per_frame
                if self.remaining_bytes <= 0:
                    logging.debug("This was the last frame so we need to change state straight to recover")
                    self.state = rx_state.RECOVER
            else:
                self.rx_frame_count = 0
                self.state = rx_state.SEARCH # We had an error decoding so back to searching for preamble
                logging.debug("RX STATE -> SEARCH: Packet loss - looking for preamble")
        if self.state == rx_state.SYNC:
            if frame.data != self.preamble: # If we don't have a preamble, no errors and our state machine is in sync, then this is likely the start of a packet
                self.state = rx_state.RECEIVE # next frames are going to be data so stop searching for preamble or header
                logging.debug("RX STATE -> RECEIVE: Found header")
                self.rx_parity = self.ParityBlock()
                self.rx_parity.add_block(frame.data)
                self.frame_errors = 0
                self.packet_data = b''
                self.remaining_bytes = int.from_bytes(frame.data[0:2], byteorder='big', signed=False)
                self.rx_length = self.remaining_bytes
                logging.debug(f"RX Remaining Bytes: {self.remaining_bytes}")
                if self.remaining_bytes > self.max_packet_size: #if we get a header that's larger than max packet size somethings gone wrong and we want to search again
                    self.state = rx_state.SEARCH
                    self.rx_frame_count = 0
                    logging.debug("RX STATE -> SEARCH: Reached end of packet")
                header = True
                frame.data = frame.data[2:] # strip off the header
            # we let the RECEIVE process directly after sync to handle the data sans the header
        if self.state == rx_state.RECEIVE and (frame.valid and frame.sync == True):
            if header != True:
                self.rx_parity.add_block(frame.data)
            self.rx_frame_count += 1
            self.packet_data += frame.data[:min(len(frame.data), self.remaining_bytes)] # append the frame bytes to the packet, unless we are past the length
            self.remaining_bytes -= min(len(frame.data), self.remaining_bytes)
            logging.debug(f"RX Remaining Bytes: {self.remaining_bytes}")
            if self.remaining_bytes == 0: # At this point we received all the data we need
                if self.frame_errors == 1:
                    logging.debug("RX STATE -> RECOVER End of packet, but was 1 frame error. Attempting to recover from parity")
                    self.state = rx_state.RECOVER
                else:
                    logging.debug(f"Calculated Parity: {bytes(self.rx_parity.parity_block).hex()}")
                    self.state = rx_state.PARITY
                    logging.debug("RX STATE -> SEARCH: Reached end of packet")
                    logging.info(f"RXed Packet: {self.packet_data}")
                    logging.info(f"RXed Packet HEX: {self.packet_data.hex()}")
                    self.callback(self.packet_data)
        elif self.state == rx_state.PARITY: # we do this to prevent
            logging.debug("Received parity frame but we don't need it.")
            self.state = rx_state.SEARCH
            self.rx_frame_count = 0



        if self.modem.sync == False and self.rx_locked == True:
            try:
                self.rx_locked = False
                self.lock.release()
                logging.debug("Unlocked TX")
            except RuntimeError:
                pass
        elif self.modem.sync == True and self.rx_locked == False:
            self.rx_locked = self.lock.acquire(blocking=False)
            if self.rx_locked:
                logging.debug("Inhibited TX as possible RX")

            
    def tx(self, packets: List[bytes]):
        self.tx_buffer.extend(packets)

    def process_queue(self):
        if not self.tx_buffer: #only run if we have something in the queue
            return

        self.lock.acquire()
        self.tx_lock.acquire()
        if type(self.stream_tx) == pyaudio.Stream:
            self.stream_tx.start_stream()
        if self.rig:
            self.rig.ptt_enable()

        for x in range(0, self.preamble_frame_count-1):
            self.modulate_tx(self.preamble)

        sent = 0
        while self.tx_buffer and (sent <= self.max_packets or self.max_packets == -1 or self.max_packets == 0): # TODO check for -1 here
            sent += 1
            packet = self.tx_buffer.pop(0)
            logging.info(f"TXing packet: {packet}")
            logging.info(f"TXing packet HEX: {packet.hex()}")
            self.modulate_tx(self.preamble)
            header = len(packet).to_bytes(2, byteorder='big', signed=False) +  packet[:self.modem.bytes_per_frame-2]
            header += b'\x00' * (self.modem.bytes_per_frame - len(header)) # pad out if short
            self.modulate_tx(header)
            parity = self.ParityBlock()
            parity.add_block(header)

            #get the remaining packet data as frames (skipping the header frame)
            tx_frame_count = 1
            for offset in range(self.modem.bytes_per_frame-2, len(packet), self.modem.bytes_per_frame):
                frame = packet[offset:offset+self.modem.bytes_per_frame]
                frame += b'\x00' * (self.modem.bytes_per_frame - len(frame)) # pad out if short
                parity.add_block(frame)
                self.modulate_tx(frame, tx_frame_count)
                tx_frame_count += 1
            
            self.modulate_tx(bytes(parity.parity_block))
        for x in range(0,self.postamble_frame_count -1):
            self.modulate_tx(bytes(parity.parity_block))

        self.tx_sample_state = None

        if self.rig:
            self.rig.ptt_disable()
        if type(self.stream_tx) == pyaudio.Stream:
            self.stream_tx.stop_stream()
        self.lock.release()
        if self.post_tx_wait_max != 0:
            time.sleep(random.uniform(self.post_tx_wait_min, self.post_tx_wait_max)) # add a random amount of 2 seconds as a back off
        self.tx_lock.release()


    def modulate_tx(self, frame: bytes, tx_frame_count=0):

        # turn the packeterized frames into modulated audio
        modulated_frame = self.modem.modulate(frame, tx_frame_count)
        
        (modulated_frame, self.tx_sample_state) = audioop.ratecv(modulated_frame,2,1,self.modem_sample_rate, self.audio_sample_rate, self.tx_sample_state)
        
        self.stream_tx.write(modulated_frame)

    class ParityBlock():
        def __init__(self):
            self.parity_block = None
        def add_block(self,bytes_in):
            logging.debug(f"Adding block for parity: {bytes_in.hex()}")
            if self.parity_block == None:
                self.parity_block = bytearray(bytes_in)
            else:
                for index, single_byte in enumerate(bytearray(bytes_in)):
                    self.parity_block[index] = (bytes_in[index] ^ self.parity_block[index]) 

    class TXThread(threading.Thread):
        def __init__(self,process_queue):
            threading.Thread.__init__(self)
            self._running = True
            self.process_queue = process_queue
        def run(self):
            while self._running == True:
                self.process_queue()
                time.sleep(0.01)

        def terminate(self):
            self._running = False