#!/usr/bin/env python3

import kissfix # TODO do we need to worry about the python3 issue / kissfix
import serial
import os, pty, serial, tty, termios
import threading
import logging

# This deals with encoding and decoding KISS frames

class KissInterface():
    def __init__(self, callback):
        self.k = kissfix.SerialKISS('/dev/ptmx', 9600) 
        self.k.start()

        # Override the serial interface with our own PTY file descriptor
        self.control, user_port = pty.openpty()
        self.ttyname = os.ttyname(user_port)
        self.k.interface.fd = self.control # we need to override the the serial port with the fd from pty
        tty.setraw(self.control, termios.TCSANOW) # this makes the tty act more like a serial port

        self.rx_thread = KissThread(callback, self.k)
        self.rx_thread.setDaemon(True)
        self.rx_thread.start()
    
    def tx(self, bytes_in: bytes):
        frame = kissfix.FEND + b'\00' + kissfix.escape_special_codes(bytes_in) + kissfix.FEND
        os.write(self.control, frame)


class KissTCPInterface():
    def __init__(self, callback):
        self.k = kissfix.TCPServerKISS('0.0.0.0', 8001) 
        self.k.start()

        self.rx_thread = KissThread(callback, self.k)
        self.rx_thread.setDaemon(True)
        self.rx_thread.start()
    
    def tx(self, bytes_in: bytes):
        try:
            frame = kissfix.FEND + b'\00' + kissfix.escape_special_codes(bytes_in) + kissfix.FEND
            self.k._write_handler(frame)
        except:
            logging.info("Issue send frame to TCP TNC - Client not connected?")
            pass # so many things can go wrong here
        

class KissThread(threading.Thread):
    def __init__(self,callback, interface):
        threading.Thread.__init__(self)
        self.callback = callback
        self._running = True
        self.interface = interface
    def run(self):
        while self._running == True:
            # check TNC port
            for frame in self.interface.read(readmode=False):
                self.callback(bytes(frame[1:])) #we strip the first two byte which is TNC port number.
    def terminate(self):
        self._running = False

