#!/usr/bin/env python3

import kissfix # TODO do we need to worry about the python3 issue / kissfix
import serial
import os, pty, serial, tty, termios
import threading

# This deals with encoding and decoding KISS frames

class KissInterface():
    def __init__(self, callback):
        self.k = kissfix.SerialKISS('/dev/ptmx', 9600) # TODO add TCP somehow?
        self.k.start()

        # Override the serial interface with our own PTY file descriptor
        self.control, user_port = pty.openpty()
        self.ttyname = os.ttyname(user_port)
        k.interface.fd = control # we need to override the the serial port with the fd from pty
        tty.setraw(control, termios.TCSANOW) # this makes the tty act more like a serial port

        self.rx_thread = KissThread(self.callback)
        self.rx_thread.setDaemon(True)
        self.rx_thread.start()
        self.callback = callback
    
    def tx(self, bytes_in: bytes):
        frame = kissfix.FEND + b'\0F' + kissfix.escape_special_codes(bytes_in) + kissfix.FEND
        os.write(self.control, frame)

class KissThread(threading.Thread):
    def __init__(self,callback):
        threading.Thread.__init__(self)
        self.callback
        self._running = True
    def run(self):
        if self._running == True:
            # check TNC port
            for frame in k.read(readmode=False):
                self.callback(bytes(frame[1:])) #we strip the first byte which is TNC port number. Old implementation required removing two bytes?
    def terminate(self):
        self._running = False

