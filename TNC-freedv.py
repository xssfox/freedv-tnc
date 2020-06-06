import ctypes
from ctypes import *
import pathlib
import pyaudio
import io
import os, pty, serial, tty, termios
import kissfix
import threading
import socket

SAMPLE_RATE = 8000 #TODO we need to work out how to resample for devices without pulse audio.
AUDIO_DEVICE_INPUT = "pulse"
AUDIO_DEVICE_OUTPUT = "pulse"
PREAMBLE_FRAMES = 20
POSTAMBLE_FRAMES = 4
MAX_PACKET_SIZE = 1500
CLEAR_FOR = 5

MAX_PACKETS_ONE_TX = 8

libname = pathlib.Path().absolute() / "build_linux/src/libcodec2.so"
c_lib = ctypes.CDLL(libname)

c_lib.freedv_open.restype = POINTER(c_ubyte)
c_lib.freedv_get_bits_per_modem_frame.restype = c_int
c_lib.freedv_get_n_nom_modem_samples.restype = c_int

c_lib.freedv_get_bits_per_modem_frame.restype = c_int
c_lib.freedv_get_n_max_modem_samples.restype = c_int
c_lib.freedv_nin.restype = c_int
c_lib.freedv_get_uncorrected_errors.restype = c_int
c_lib.freedv_get_sync.restype = c_int
c_lib.freedv_get_uncorrected_errors.restype = c_int

freedv = c_lib.freedv_open(7)  #7 -  700D

bytes_per_frame = int(c_lib.freedv_get_bits_per_modem_frame(freedv)/8)

mod_in_type = c_short * c_lib.freedv_get_n_max_modem_samples(freedv)

mod_out = c_short * c_lib.freedv_get_n_nom_modem_samples(freedv)
mod_out = mod_out()

bytes_out = (c_ubyte * bytes_per_frame)
bytes_out_type = (c_ubyte * bytes_per_frame)
bytes_out = bytes_out()

bytes_in = (c_ubyte * bytes_per_frame)
bytes_in_type = (c_ubyte * bytes_per_frame)
bytes_in = bytes_in()


c_lib.freedv_rawdatarx.argtype = [POINTER(c_ubyte), bytes_out, mod_in_type]
c_lib.freedv_rawdatatx.argtype = [POINTER(c_ubyte), mod_out, bytes_in]

data_to_send=[]

# We need to work out how many samples the demod needs on each
# call (nin).  This is used to adjust for differences in the tx
# and rx sample clock frequencies.  Note also the number of
# output speech samples "nout" is time varying.

nin = c_lib.freedv_nin(freedv)


# audio setup

p = pyaudio.PyAudio()
for x in range(0,p.get_device_count()):
    if p.get_device_info_by_index(x)["name"] == AUDIO_DEVICE_INPUT:
        in_dev = x
    if p.get_device_info_by_index(x)["name"] == AUDIO_DEVICE_OUTPUT:
        out_dev = x
    print(f'{x} - {p.get_device_info_by_index(x)["name"]}')


stream_in = p.open(format=pyaudio.paInt16, 
                channels=1,
                rate=SAMPLE_RATE,
                frames_per_buffer=c_lib.freedv_get_n_max_modem_samples(freedv),
                input=True,
                input_device_index=in_dev
                )
stream_out = p.open(format=pyaudio.paInt16, 
                channels=1,
                rate=SAMPLE_RATE,
                frames_per_buffer=c_lib.freedv_get_n_nom_modem_samples(freedv),
                output=True,
                output_device_index=out_dev
                )

max_sample = len(mod_in_type())*sizeof(c_short)  # short == 2 bytes



# virtual serial TNC setup


k = kissfix.SerialKISS('/dev/ptmx', 9600)
k.start()
control, user_port = pty.openpty()
print(f'Our PTY is {os.ttyname(user_port)}')
k.interface.fd = control # we need to override the the serial port with the fd from pty
tty.setraw(control, termios.TCSANOW)

# our packet simple is a preamble, followed by expected length and then the data
# we also stick a tail on the end because foxes.

def build_frames(data, preamble=False):
    print(f'TX: {bytes(data).hex()}')
    #preamble
    if preamble == True:
        preamble_length = PREAMBLE_FRAMES
    else:
        preamble_length = 1 # we assume that for packets that are adjacent to others that we don't need to have more buffer
    packet = b'\xFF'*bytes_per_frame*preamble_length     # if we can get the modem to sync quicker then we can have less preamble
    packet += len(data).to_bytes(2, byteorder='big')
    packet += data
    packet += b'\x01'*bytes_per_frame*POSTAMBLE_FRAMES   # we might be able to remove these between packets
    packet = bytearray(packet)
    packet_len = len(packet)
    if packet_len % bytes_per_frame != 0:
        remaining_bytes = bytes_per_frame - (packet_len % bytes_per_frame)
        packet_len += remaining_bytes
    
    buffer = bytearray(packet_len)
    buffer[:len(packet)] = packet
    
    frames = []
    for x in range(0, len(buffer), bytes_per_frame):
        frame_start = x
        frame_end = (x)+bytes_per_frame
        frames.append(bytes_in_type.from_buffer_copy(buffer[frame_start:frame_end]))

      

    return frames


# setup thread for reading off serial

class ThreadKiss(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        global data_to_send
        while True:
            # check TNC port
            for frame in k.read(readmode=False, ):
                #print(frame[2:]) #why do we need to remove 2 bytes though?
                data_to_send.append(bytes(frame[2:])) #  the first byte is for which TNC port to go out. We only support one, so everything goes out


tkiss = ThreadKiss()
tkiss.setDaemon(True)
tkiss.start()

#data_to_send += build_frames(b'HELLO WORLD!!!!')


clear_for = 0

# rigctl - https://github.com/darksidelemm/rotctld-web-gui/blob/master/rotatorgui.py#L35

class RIGCTLD(object):
    """ rotctld (hamlib) communication class """
    # Note: This is a massive hack. 

    def __init__(self, hostname="localhost", port=4532, poll_rate=5, timeout=5):
        """ Open a connection to rotctld, and test it for validity """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)

        self.hostname = hostname
        self.port = port
        self.connect()

    def get_model(self):
        """ Get the rotator model from rotctld """
        model = self.send_command(b'_')
        return model

    def connect(self):
        """ Connect to rotctld instance """
        self.sock.connect((self.hostname,self.port))
        model = self.get_model()
        if model == None:
            # Timeout!
            self.close()
            raise Exception("Timeout!")
        else:
            return model


    def close(self):
        self.sock.close()


    def send_command(self, command):
        """ Send a command to the connected rotctld instance,
            and return the return value.
        """
        self.sock.sendall(command+b'\n')
        try:
            return self.sock.recv(1024)
        except:
            return None

    def key_down(self):
        self.send_command(b"T 1")

    def key_up(self):
        self.send_command(b"T 0")




rigctl = RIGCTLD()

last_frame_was_preamble = False
bytes_remaining = 0
packet_out=bytearray()

while 1:

    # RX LOOP
    sample = stream_in.read(nin)
    # we need to pad out the buffer to make ctypes happy
    buffer = bytearray(len(mod_in_type())*sizeof(c_short))
    buffer[:len(sample)] = sample

    nin = c_lib.freedv_nin(freedv)
    #stream._frames_per_buffer = nin # I'm not sure if I should be allowed to set this :O 
    mod_in = mod_in_type.from_buffer_copy(buffer)
    bytes_out = bytes_out_type()
    c_lib.freedv_rawdatarx(freedv, bytes_out, mod_in)
    #print(
    #    f'sync: {c_lib.freedv_get_sync(freedv)} uncorrected_errors: {c_lib.freedv_get_uncorrected_errors(freedv)}')

    if not c_lib.freedv_get_uncorrected_errors(freedv) and c_lib.freedv_get_sync(freedv) == 1:
        bytes_out = bytearray(bytes_out)
        #print(bytes_out)
        if last_frame_was_preamble == False and bytes_out == bytearray(b'\xFF'*bytes_per_frame):
            #print("preamble detected")
            last_frame_was_preamble = True
        if last_frame_was_preamble == True and bytes_out != bytearray(b'\xFF'*bytes_per_frame):
            length = int.from_bytes(bytes_out[0:2], byteorder='big')
            if bytes_remaining < 1600: # it's possible that we accidentally decode the length incorrectly so we put some bounds on how long it can be and hopefully this will self correct from higher levels
                bytes_remaining = length
                #print(f"found start of packet... maybe... it should be {bytes_remaining} long")
                bytes_out = bytes_out[2:]
                last_frame_was_preamble = False
        if bytes_remaining > 0: # RX packet
            data_size = min(len(bytes_out), bytes_remaining)
            #print(data_size)
            #print(bytes_remaining)
            #print(f'part:{bytes_out[:data_size]}')
            packet_out = packet_out + bytes_out[:data_size]
            bytes_remaining = bytes_remaining - data_size
            if bytes_remaining == 0:
                #print("success, we might of decoded a packet")
                
                packet_out = bytes(packet_out)
                print(f'RX: {packet_out.hex()}')
                frame = kissfix.FEND + b'\0F' + kissfix.escape_special_codes(packet_out) + kissfix.FEND
                os.write(control, frame)
                bytes_remaining = 0
                packet_out = bytearray()
                last_frame_was_preamble = False #reset state if we loose sync because we've lost the packet
    else:
        # if bytes_remaining != 0 or last_frame_was_preamble != False:
        #     print("lost sync, reseting state")
        bytes_remaining = 0
        packet_out = bytearray()
        last_frame_was_preamble = False #reset state if we loose sync because we've lost the packet

    if c_lib.freedv_get_sync(freedv) == 0:
        clear_for = clear_for + 1
    elif clear_for > 0:
        clear_for = 0

    # Check if we have data to send and if it's clear to send
    if len(data_to_send) > 0 and c_lib.freedv_get_sync(freedv) == 0 and clear_for > CLEAR_FOR:
        stream_in.stop_stream() # stop RX audio so we don't build up a buffer
        rigctl.key_down()
        preamble = True
        to_send = data_to_send[:MAX_PACKETS_ONE_TX]
        del data_to_send[:MAX_PACKETS_ONE_TX]
        stream_out.start_stream()
        for packet in to_send:
            frames = build_frames(packet, preamble)
            preamble = False
            for bytes_in in frames:
                #print(f"sending {bytes(bytes_in)}")
                c_lib.freedv_rawdatatx(freedv,mod_out,bytes_in) #encode
                stream_out.write(bytes(bytearray(mod_out))) # write to sound card
        stream_out.stop_stream()
        rigctl.key_up()
        stream_in.start_stream() # restart rx audio
        clear_for = -CLEAR_FOR*3 # set this to a negative number so it takes awhile before to give the next station a chance

