#!/usr/bin/env python3
import socket

# rigctl - https://github.com/darksidelemm/rotctld-web-gui/blob/master/rotatorgui.py#L35

class Rigctld():
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

    def ptt_enable(self):
        self.send_command(b"T 1")

    def ptt_disable(self):
        self.send_command(b"T 0")