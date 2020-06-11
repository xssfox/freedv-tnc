#!/usr/bin/env python3
from . import tnc, rigctl, freedv, rf
import platform
import logging
from threading import Lock
import random
import argparse
import sys

def main():
    logger = logging.getLogger()



    parser = argparse.ArgumentParser(description='FreeDV Data Modem TNC')

    parser.add_argument('--modem', dest="modem", default="700D", choices=['700D'], help="The FreeDV Modem to use. Currently only 700D is supported", type=str)
    parser.add_argument('--rx-sound-device', dest="rx_sound_device", default=False, help="The sound card used to rx", type=str)
    parser.add_argument('--tx-sound-device', dest="tx_sound_device", default=False, help="The sound card used to tx", type=str)
    parser.add_argument('--list-sound-devices', dest="list_sound_devices", action='store_true', help="List audio devices")
    parser.add_argument('--sample-rate', dest="sample_rate", default=44100, help="Sample rate of the soundcard.", type=int)
    parser.add_argument('--rigctl-hostname', dest="rigctl_hostname", default='localhost', help="Hostname or IP of the rigctld server", type=str)
    parser.add_argument('--rigctl-port', dest="rigctl_port", default=4532, help="Port for rigtctld", type=int)
    parser.add_argument('--vox', action='store_true', help="Disables rigctl", dest="vox")
    parser.add_argument('--no-pty', action='store_false', help="Disables serial port", dest="pty")
    parser.add_argument('--no-tx', action='store_false', help="Disables serial port", dest="tx")
    parser.add_argument('-v', action='store_true', help="Enables debug log", dest="verbose")
    parser.add_argument('--stdout', action='store_true', help="RX data stdout", dest="stdout")
    parser.add_argument('--tcp', action='store_true', help="Enabled the TCP Server (listens on 0.0.0.0:8001", dest="tcp")

    group = parser.add_argument_group(title='Advanced TNC Tuneable', description="These settings can be used to tune the TNC")
    group.add_argument('--preamble-length', dest="preamble_length", default=10, help="Number of preamble frames to send", type=int)
    group.add_argument('--min-tx-wait', dest="min_tx_wait", default=5, help="The TNC will wait this value in seconds + a random amount of seconds between 0 and 2", type=int)

    args = parser.parse_args()

    if args.list_sound_devices:
        for line in rf.list_audio_devices():
            print(line)
        sys.exit(0)


    tx_inhibit = Lock()

    def kiss_rx_callback(frame: bytes):
        logging.debug(f"Received KISS frame: {frame.hex()}")
        tx_inhibit.acquire()
        if args.tx:
            radio.tx([frame]) #TODO : this is where we could send multiple packets but a lot of protocols except one at a time
        tx_inhibit.release()

    def rf_rx_callback(packet: bytes):
        logging.debug(f"Received RF packet: {packet.hex()}")
        if tnc_interface:
            tnc_interface.tx(packet)
        if tcp_interface:
            tcp_interface.tx(packet)
        if args.stdout:
            sys.stdout.buffer.write(packet)
            sys.stdout.flush()
        
    if args.vox:
        rig = None
    else:
        try:
            rig = rigctl.Rigctld(hostname=args.rigctl_hostname, port=args.rigctl_port)
        except ConnectionRefusedError:
            logger.error("Could not connect to rigtld - did you mean to use --vox?")
            sys.exit()

    if args.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    try:
        modem = freedv.FreeDV()
    except OSError:
        logger.error("Could not find libcodec2 - please ensure it's installed and ldconfig has been run")
        sys.exit(0)

    if sys.platform == "darwin":
        logger.info("Disabling pty due to running on darwin")
        args.pty = False


    if args.pty:
        tnc_interface = tnc.KissInterface(kiss_rx_callback)
        logger.info(f'TNC port is at : {tnc_interface.ttyname}')
    else:
        tnc_interface = None

    if args.tcp:
        tcp_interface = tnc.KissTCPInterface(kiss_rx_callback)
    else:
        tcp_interface = None

    if args.tx == False:
        args.tx_sound_device = None 
    try:
        radio = rf.Rf( 
                        rx_device=args.rx_sound_device,
                        tx_device=args.tx_sound_device,
                        audio_sample_rate=args.sample_rate,
                        modem=modem,
                        callback=rf_rx_callback,
                        rig=rig,
                        preamble_frame_count=args.preamble_length,
                        post_tx_wait=args.min_tx_wait
                    )
    except UnboundLocalError:
       logger.error("Couldn't intialize RF. Likely your soundcard isn't avaliable")
       sys.exit()

    while True:
        radio.rx()

if __name__ == "__main__":
    main()