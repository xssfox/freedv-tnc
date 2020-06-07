#!/usr/bin/env python3
import pyaudio
from enum import Enum
from typing import List

# This deals with all the RF things and resampling

# Packet structure
#
#
# preamble 0xFF * bytes_per_frame * preamble frame count - needed to get modem sync
# packet_length, 2 bytes, unsigned big
# data + padding to fit event even frame count
# postamble 0x01 * bytes_per_frame this isn't strictly needed but is suggested to allow the modem to finish decoding the last frame. between packets don't send this, just the preamble

class rx_state(Enum): # might make a proper state machine later
    SEARCH = 1
    SYNC =  2
    RECEIVE = 3

class Rf():
    def __init__(self, 
                    modem,
                    callback,
                    rx_device="default",
                    tx_device=None,
                    sample_rate=8000,
                    max_packet_size=3000,
                    preamble_frame_count=20,
                    postamble_frame_count=4,
                    rig=None
                ):
        
        self.state = rx_state.SEARCH
        self.max_packet_size = max_packet_size

        self.modem=modem

        self.preamble = b'\xFF' * modem.bytes_per_frame
        self.postamble = b'\x01' * modem.bytes_per_frame
        self.preamble_frame_count = preamble_frame_count
        self.postamble_frame_count = postamble_frame_count
        self.callback = callback

        self.p = pyaudio.PyAudio()
        # Find audio interface from name
        for x in range(0,p.get_device_count()):
            if p.get_device_info_by_index(x)["name"] == rx_device:
                rx_dev = x
            if p.get_device_info_by_index(x)["name"] == tx_device:
                tx_dev = x
        self.stream_rx = p.open(format=pyaudio.paInt16, 
                        channels=1,
                        rate=sample_rate,
                        frames_per_buffer=modem.get_n_max_modem_samples(),
                        input=True,
                        input_device_index=rx_dev
                    )
        if tx_device:
            self.stream_tx = p.open(format=pyaudio.paInt16, 
                            channels=1,
                            rate=sample_rate,
                            frames_per_buffer=modem.get_n_nom_modem_samples(),
                            output=True,
                            output_device_index=tx_dev
                        )



    def rx(self):
        audio_sample = stream_in.read(modem.nin)

        frame = modem.demodulate(audio_sample)
        # RX statemachine
        # If we are in search mode we are looking for preambles
        if self.state == rx_state.SEARCH:
            if frame.data == self.preamble and frame.uncorrected_errors == 0 and frame.sync == True:
                self.state = rx_state.SYNC # the next frame could be a packet
        
        if frame.uncorrected_errors > 0 and frame.sync == False:
            self.state = rx_state.SEARCH # We had an error decoding so back to searching for preamble
        
        if self.state == rx_state.SYNC:
            if frame.data != self.preamble: # If we don't have a preamble, no errors and our state machine is in sync, then this is likely the start of a packet
                self.state = rx_state.RECEIVE # next frames are going to be data so stop searching for preamble or header

                self.remaining_bytes = int.from_bytes(frame[0:2], byteorder='big', signed=False)
                if self.remaining_bytes > self.max_packet_size: #if we get a header that's larger than max packet size somethings gone wrong and we want to search again
                    rx_state.SEARCH
                frame = frame[2:] # strip off the header
            # we let the RECEIVE process directly after sync to handle the data sans the header
        
        if self.rx_state == rx_state.RECEIVE:
            self.packet_data += frame[:min(len(frame), self.remaining_bytes)] # append the frame bytes to the packet, unless we are past the length
            self.remaining_bytes -= min(len(frame), self.remaining_bytes)
            if self.remaining_bytes == 0: # At this point we received all the data we need
                self.state = rx_state.RECEIVE
                self.callback(packet_data)
            
    def tx(self, packets: List[bytes]):
        # We take a list of packets as we want to control the preambles between them to optimize RF time
        frames = []

        frames.extend([preamble] * (preamble_frame_count - 1)) #each packet will start with a preamble so we can exclude it from the long start preamble

        for packet in packets:
            frames.append(preamble)
            header = len(packet).to_bytes(2, byteorder='big', signed=False) +  packet[:modem.bytes_per_frame]
            frames.append(header)

            #get the remaining packet data as frames (skipping the header frame)
            for offset in range(modem.bytes_per_frame-2, len(packet), modem.bytes_per_frame):
                frames.append(packet[offset:offset+modem.bytes_per_frame])
            
            frames.append(self.postamble)

        # turn the packeterized frames into modulated audio
        modulated_frames = []
        for frame in frames:
            modulated_frames.append(modem.modulate(frame))

        # rx the audio
        stream_tx.start_stream()
        if rig:
            rig.ptt_enable()
        stream_tx.write(modulated_frames)
        if rig:
            rig.ptt_disable()
        stream_tx.stop_stream()

