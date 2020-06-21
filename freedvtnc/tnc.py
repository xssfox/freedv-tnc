#!/usr/bin/env python3

import kissfix # TODO do we need to worry about the python3 issue / kissfix
import serial
import os, pty, serial, tty, termios
import threading
import logging
import sys, traceback
import fcntl

# This deals with encoding and decoding KISS frames

class KissInterface():
    def __init__(self, callback):
        self.k = kissfix.SerialKISS('/dev/ptmx', 9600) 
        self.k.start()

        # Override the serial interface with our own PTY file descriptor
        self.control, self.user_port = pty.openpty()
        self.ttyname = os.ttyname(self.user_port)
        self.k.interface.fd = self.control # we need to override the the serial port with the fd from pty
        tty.setraw(self.control, termios.TCSANOW) # this makes the tty act more like a serial port

        # change flags to be non blocking so that buffer full doesn't cause issues
        flags = fcntl.fcntl(self.control, fcntl.F_GETFL)
        flags |= os.O_NONBLOCK
        fcntl.fcntl(self.control, fcntl.F_SETFL, flags)

        self.rx_thread = KissThread(callback, self.k)
        self.rx_thread.setDaemon(True)
        self.rx_thread.start()
    
    def tx(self, bytes_in: bytes):
        frame = kissfix.FEND + b'\00' + kissfix.escape_special_codes(bytes_in) + kissfix.FEND
        try:
            os.write(self.control, frame)
        except BlockingIOError:
            logging.error("PTY interface buffer is full. The connected application may have crashed or isn't reading fast enough. Data loss is likely. Alternatively you aren't using the PTY interface and should have used --no-pty. Clearing the buffer now so we can keep going")
            blocking = os.get_blocking(self.user_port) # remember what the state was before
            os.set_blocking(self.user_port, False)
            try:
                while 1:
                    os.read(self.user_port,32) # read off the buffer until we've cleared it
            except BlockingIOError:
                pass
            os.set_blocking(self.user_port, blocking) # restore the state after          


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
            traceback.print_exc(file=sys.stderr)
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
