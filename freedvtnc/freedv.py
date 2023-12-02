#!/usr/bin/env python3

import ctypes
from ctypes import *
import logging
import sys
import array
import struct
from threading import Lock

lock = Lock()

# def crc_16(msg):
#     lo = hi = 0xff
#     mask = 0xff
#     for new in msg:
#         new ^= lo
#         new ^= (new << 4) & mask
#         tmp = new >> 5
#         lo = hi
#         hi = new ^ tmp
#         lo ^= (new << 3) & mask
#         lo ^= new >> 4
#     lo ^= mask
#     hi ^= mask
#     return hi << 8 | lo

class Frame():
    def __init__(self,valid: bool, sync: bool, data: bytes, count: int, rx_status: int):
        self.valid = valid
        self.sync = sync
        self.data = data
        self.count = count
        self.rx_status = rx_status

class FreeDV():
    def __init__(self,mode="700D", libpath=f"libcodec2"):
        
        if sys.platform == "darwin":
            libpath += ".dylib"
        else:
            libpath += ".so"
        try:
            self.c_lib = ctypes.cdll.LoadLibrary(libpath) # future improvement would be to try a few places / names
        except OSError:
            self.c_lib = ctypes.cdll.LoadLibrary(f"/usr/local/lib/{libpath}")

        self.c_lib.freedv_open.restype = POINTER(c_ubyte)
        self.c_lib.freedv_open.argtype = [c_int]
        self.c_lib.freedv_get_bits_per_modem_frame.restype = c_int
        self.c_lib.freedv_get_n_nom_modem_samples.restype = c_int

        

        self.c_lib.freedv_get_n_max_modem_samples.restype = c_int

        self.c_lib.freedv_nin.restype = c_int

        self.c_lib.freedv_get_sync.argtype = POINTER(c_ubyte)
        self.c_lib.freedv_get_sync.restype = c_int

        self.c_lib.freedv_get_rx_status.argtype = POINTER(c_ubyte)
        self.c_lib.freedv_get_rx_status.restype = c_int


        self.raw_sync = c_int()
        self.raw_snr = c_float()

        self.c_lib.freedv_get_modem_stats.argtype = [POINTER(c_ubyte), POINTER(c_int), POINTER(c_float)]

        if mode == "700D":
            self.freedv = self.c_lib.freedv_open(7)  #7 -  700D
        elif mode == "700E":
            self.freedv = self.c_lib.freedv_open(13)  #13 -  700E
        elif mode == "DATAC0":
            self.freedv = self.c_lib.freedv_open(14)  
        elif mode == "DATAC1":
            self.freedv = self.c_lib.freedv_open(10)   
        elif mode == "DATAC3":
            self.freedv = self.c_lib.freedv_open(12)  
        elif mode == "DATAC4":
            self.freedv = self.c_lib.freedv_open(18)  
        elif mode == "DATAC13":
            self.freedv = self.c_lib.freedv_open(19) 
        elif mode == "FSK_LDPC":
            self.freedv = self.c_lib.freedv_open(9)  
        else:
            raise NotImplementedError("Only some modems are currently supported")
        
        self.c_lib.freedv_get_mode.argtype = [POINTER(c_ubyte)]
        self.c_lib.freedv_get_mode.restype = c_int
        self.reported_mode = self.c_lib.freedv_get_mode(self.freedv)

        self.c_lib.freedv_get_modem_sample_rate.argtypes =  [POINTER(c_ubyte)]
        self.c_lib.freedv_get_modem_sample_rate.restype = c_int

        self.modem_sample_rate = self.c_lib.freedv_get_modem_sample_rate(self.freedv)

        logging.debug(f"Opened mode {self.reported_mode} with samplerate {self.modem_sample_rate}")


        self.c_lib.freedv_set_verbose.argtype = [POINTER(c_ubyte), c_int]
        self.c_lib.freedv_set_verbose(self.freedv, 1)

        self.bytes_per_frame = int(self.c_lib.freedv_get_bits_per_modem_frame(self.freedv)/8) - 2 # 2 bytes / 16 bits for crc checksum
        logging.debug(f"Usable bytes per frame {self.bytes_per_frame}")
        self.c_lib.freedv_rawdatarx.argtype = [POINTER(c_ubyte), POINTER(self.FrameBytes()), POINTER(self.ModulationIn())]
        self.c_lib.freedv_rawdatarx.restype = c_int
        self.c_lib.freedv_rawdatatx.argtype = [POINTER(c_ubyte), POINTER(self.ModulationOut()), POINTER(self.FrameBytes())]

        base_scramble = b'\xbd\xe5\xa2\xd7\xa5\x72\x02\x3b\x86\x3d\xdd\x7b\xb5\xd8\xc4\x75'
                        # if the modem bytes per frame is larger than our preable we repeat, if not we truncate
        self.scramble_pattern = bytearray((base_scramble * max(1,int( self.bytes_per_frame/ len(base_scramble)+1)))[:self.bytes_per_frame])

        # Enabling clipping
        #self.c_lib.freedv_set_clip(self.freedv, 1)

        self.c_lib.freedv_gen_crc16.argtype = [POINTER(self.FrameBytes()), c_int]
        self.c_lib.freedv_gen_crc16.restype = c_ushort


        logging.debug(f"Initialized FreeDV Modem {self.bytes_per_frame + 2}")

        self.nin = 0
        self.update_nin() #initial nin value.

        self.mod_in = self.ModulationIn()()
        self.mod_out = self.ModulationOut()()
        self.din = self.FrameBytes()()
        self.dout = self.FrameBytes()()

    @property
    def rx_status(self):
        return self.c_lib.freedv_get_rx_status(self.freedv)
        
    @property
    def snr(self):
        return float(self.raw_snr.value)

    @property
    def sync(self):
        return bool(self.raw_sync.value)

    def CRC(self, frame_in):
        crc = self.c_lib.freedv_gen_crc16(frame_in,self.bytes_per_frame).to_bytes(2, byteorder="big")
        return bytes(crc)
        
    def ModulationIn(self):
        return (c_short * self.c_lib.freedv_get_n_max_modem_samples(self.freedv))

    def ModulationOut(self):
        return (c_short * (self.c_lib.freedv_get_n_tx_modem_samples(self.freedv)))

    def FrameBytes(self):
        return (c_ubyte * int(self.c_lib.freedv_get_bits_per_modem_frame(self.freedv)/8))

    def get_n_max_modem_samples(self) -> int:
        return int(self.c_lib.freedv_get_n_max_modem_samples(self.freedv))
    def get_n_nom_modem_samples(self) -> int:
        return int(self.c_lib.freedv_get_n_nom_modem_samples(self.freedv))

    def update_nin(self):
        with lock:
            new_nin = int(self.c_lib.freedv_nin(self.freedv))
        if self.nin != new_nin:
            logging.debug(f"Updated nin {new_nin}")
        if new_nin == 0:
            logging.debug(f"nin 0 for some reason, setting to 1 to get unstuck") # hack - need to fix
            new_nin = 1
        self.nin = new_nin

    def demodulate(self, bytes_in: bytes, packet_num=0) -> bytes:
        # from_buffer_copy requires exact size so we pad it out.
        #logging.debug(bytes_in.hex())
        buffer = bytearray(len(self.mod_in)*2) # create empty byte array
        buffer[:len(bytes_in)] = bytes_in # copy across what we have
        #logging.debug(f"dout:{object.__repr__(self.dout)} mod_in:{object.__repr__(self.mod_in)} din:{object.__repr__(self.din)} mod_out:{object.__repr__(self.mod_out)}")
        self.mod_in[:] = struct.unpack('H'*(int(len(buffer)/2)) , buffer)
        with lock:
            bytes_rxd = self.c_lib.freedv_rawdatarx(self.freedv, self.dout, self.mod_in)
            self.c_lib.freedv_get_modem_stats(self.freedv,byref(self.raw_sync), byref(self.raw_snr))
            sync = bool(self.c_lib.freedv_get_sync(self.freedv))
        self.update_nin()
        # if not bytes_rxd:
        #     return None
        
        #logging.debug(f"RXd: {bytes_rxd}")
        bytes_out = bytes(self.dout)
        # Check checksum
        provided_checksum = bytes_out[-2:]
        bytes_out = bytes_out[:-2] 
        calculated_checksum = self.CRC(bytes_out)
        if provided_checksum == calculated_checksum and bytes_rxd:
            valid = True
            #logging.debug(f"Valid CRC: [SNR: {self.snr:.2f} SYNC: {self.sync}] {self.unscramble(bytes_out, packet_num).hex()}")
        else:
            with lock:
                if sync == True and bytes_rxd:
                    logging.debug(f"Invalid CRC: [SNR: {self.snr:.2f} SYNC: {self.sync}] {bytes_out.hex()}{provided_checksum.hex()} {calculated_checksum.hex()}")
            valid = False
        bytes_out = self.unscramble(bytes_out, packet_num) # Unscramble

        if bytes_rxd == 0:
            valid = False
        frame = Frame(
            valid=valid,
            sync=sync,
            data=bytes(bytes_out),
            count=bytes_rxd,
            rx_status=self.rx_status
        )

            
        if frame.valid == 0 and bytes_in != len(bytes_in) * b'\x00' and bytes_rxd:
            logging.debug(f"Demodulated: [SNR: {self.snr:.2f} SYNC: {self.sync}] {frame.data.hex()}")

       
    
        

        return frame

    def modulate(self, bytes_in: bytes, packet_num=0) -> bytes:
        logging.debug(f"Modulating: {bytes_in.hex()}")
        if len(bytes_in) > self.bytes_per_frame:
            raise AttributeError(f"bytes_in ({len(bytes_in)}) > than bytes_per_frame({self.bytes_per_frame}) supported by this mode")
        
        buffer = bytearray(self.bytes_per_frame+2) # pad out the frame if it's too short
        buffer[:len(bytes_in)] = bytes_in
        
        buffer[:-2] = self.scramble(buffer[:-2], packet_num)

        # Add Checksum
        #buffer += crc_16(buffer).to_bytes(2, byteorder='big')
        #buffer += b"\x00\x00"
        self.din[:] = buffer
        crc = self.CRC(self.din)
        self.din[-2] = crc[0]
        self.din[-1] = crc[1]
        #logging.debug(f"dout:{object.__repr__(self.dout)} mod_in:{object.__repr__(self.mod_in)} din:{object.__repr__(self.din)} mod_out:{object.__repr__(self.mod_out)}")
        
        logging.debug("Calling lib")
        logging.debug(bytes(self.din).hex())
        with lock:
            self.c_lib.freedv_rawdatatx(self.freedv,self.mod_out,self.din)
        logging.debug("Returned")
        return bytes(self.mod_out)

    def scramble(self, bytes_in, packet_num=0):
        output = bytearray(len(bytes_in))
        scramble_pattern = self.scramble_pattern[packet_num:] + self.scramble_pattern[:packet_num]
        for index, single_byte in enumerate(bytearray(bytes_in)):
            output[index] = (bytes_in[index] ^ scramble_pattern[index]) 
        return output

    unscramble = scramble