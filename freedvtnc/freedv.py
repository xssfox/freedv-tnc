#!/usr/bin/env python3

import ctypes
from ctypes import *
import logging
import sys

class Frame():
    def __init__(self, uncorrected_errors: int, sync: bool, data: bytes):
        self.uncorrected_errors = uncorrected_errors
        self.sync = sync
        self.data = data

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
        self.c_lib.freedv_get_bits_per_modem_frame.restype = c_int
        self.c_lib.freedv_get_n_nom_modem_samples.restype = c_int

        self.c_lib.freedv_get_bits_per_modem_frame.restype = c_int
        self.c_lib.freedv_get_n_max_modem_samples.restype = c_int

        self.c_lib.freedv_nin.restype = c_int

        self.c_lib.freedv_get_sync.restype = c_int

        # freedv_get_uncorrected_errors was renamed to freedv_get_total_packet_errors and ideally we want to support both for the moment
        try:
            self.freedv_get_uncorrected_errors = self.c_lib.freedv_get_uncorrected_errors
        except AttributeError:
            self.freedv_get_uncorrected_errors = self.c_lib.freedv_get_total_packet_errors
        
        self.freedv_get_uncorrected_errors.restype = c_int

        self.raw_sync = c_int()
        self.raw_snr = c_float()

        self.c_lib.freedv_get_modem_stats.argtype = [POINTER(c_ubyte), POINTER(c_int), POINTER(c_float)]

        if mode == "700D":
            self.freedv = self.c_lib.freedv_open(7)  #7 -  700D
        elif mode == "700E":
            self.freedv = self.c_lib.freedv_open(13)  #13 -  700E
        else:
            raise NotImplementedError("Only 700D is currently supported")

        self.bytes_per_frame = int(self.c_lib.freedv_get_bits_per_modem_frame(self.freedv)/8)

        self.c_lib.freedv_rawdatarx.argtype = [POINTER(c_ubyte), self.FrameBytes(), self.ModulationIn()]
        self.c_lib.freedv_rawdatatx.argtype = [POINTER(c_ubyte), self.ModulationOut(), self.FrameBytes()]

        base_scramble = b'\xbd\xe5\xa2\xd7\xa5\x72\x02\x3b\x86\x3d\xdd\x7b\xb5\xd8\xc4\x75'
                        # if the modem bytes per frame is larger than our preable we repeat, if not we truncate
        self.scramble_pattern = bytearray((base_scramble * max(1,int( self.bytes_per_frame/ len(base_scramble)+1)))[:self.bytes_per_frame])

        # Enabling clipping
        self.c_lib.freedv_set_clip(self.freedv, 1)

        logging.debug("Initialized FreeDV Modem")

        self.nin = 0
        self.update_nin() #initial nin value.
        
    @property
    def snr(self):
        return float(self.raw_snr.value)

    @property
    def sync(self):
        return bool(self.raw_sync.value)
        
    def ModulationIn(self):
        return c_short * self.c_lib.freedv_get_n_max_modem_samples(self.freedv)

    def ModulationOut(self):
        return (c_short * self.c_lib.freedv_get_n_nom_modem_samples(self.freedv))

    def FrameBytes(self):
        return (c_ubyte * self.bytes_per_frame)

    def get_n_max_modem_samples(self) -> int:
        return int(self.c_lib.freedv_get_n_max_modem_samples(self.freedv))
    def get_n_nom_modem_samples(self) -> int:
        return int(self.c_lib.freedv_get_n_nom_modem_samples(self.freedv))

    def update_nin(self):
        new_nin = int(self.c_lib.freedv_nin(self.freedv))
        if self.nin != new_nin:
            logging.debug(f"Updated nin {new_nin}")
        self.nin = new_nin

    def demodulate(self, bytes_in: bytes, packet_num=0) -> bytes:
        # from_buffer_copy requires exact size so we pad it out.
        buffer = bytearray(len(self.ModulationIn()())*sizeof(c_short)) # create empty byte array
        buffer[:len(bytes_in)] = bytes_in # copy across what we have

        modulation = self.ModulationIn() # get an empty modulation array
        modulation = modulation.from_buffer_copy(buffer) # copy buffer across and get a pointer to it.

        bytes_out = self.FrameBytes()() # initilize a pointer to where bytes will be outputed
        
        self.c_lib.freedv_rawdatarx(self.freedv, bytes_out, modulation)

        bytes_out = self.unscramble(bytes_out, packet_num) # Unscramble

        frame = Frame(
            uncorrected_errors=int(self.freedv_get_uncorrected_errors(self.freedv)),
            sync=bool(self.c_lib.freedv_get_sync(self.freedv)),
            data=bytes(bytes_out)
        )

        self.c_lib.freedv_get_modem_stats(self.freedv,byref(self.raw_sync), byref(self.raw_snr))
        
        if frame.sync == True and frame.uncorrected_errors == 0 and bytes_in != len(bytes_in) * b'\x00':
            logging.debug(f"Demodulated: [SNR: {self.snr:.2f} SYNC: {self.sync}] {frame.data.hex()}")

       
    
        self.update_nin()

        return frame

    def modulate(self, bytes_in: bytes, packet_num=0) -> bytes:
        logging.debug(f"Modulating: {bytes_in.hex()}")
        if len(bytes_in) > self.bytes_per_frame:
            raise AttributeError(f"bytes_in ({len(bytes_in)}) > than bytes_per_frame({self.bytes_per_frame}) supported by this mode")
        
        buffer = bytearray(self.bytes_per_frame) # pad out the frame if it's too short
        buffer[:len(bytes_in)] = bytes_in

        buffer = self.scramble(buffer, packet_num)

        modulation = self.ModulationOut()() # new modulation object and get pointer to it
        
        mod_bytes = self.FrameBytes().from_buffer_copy(buffer)

        self.c_lib.freedv_rawdatatx(self.freedv,modulation,mod_bytes)

        return bytes(modulation)

    def scramble(self, bytes_in, packet_num=0):
        output = bytearray(len(bytes_in))
        scramble_pattern = self.scramble_pattern[packet_num:] + self.scramble_pattern[:packet_num]
        for index, single_byte in enumerate(bytearray(bytes_in)):
            output[index] = (bytes_in[index] ^ self.scramble_pattern[index]) 
        return output

    unscramble = scramble