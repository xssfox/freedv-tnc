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
    parser.add_argument('--rx-sound-device', dest="rx_sound_device", default="default", help="The sound card used to rx", type=str)
    parser.add_argument('--tx-sound-device', dest="tx_sound_device", default="default", help="The sound card used to tx", type=str)
    parser.add_argument('--list-sound-devices', dest="list_sound_devices", action='store_true', help="List audio devices")
    parser.add_argument('--sample-rate', dest="sample_rate", default=44100, help="Sample rate of the soundcard.", type=int)
    parser.add_argument('--rigctl-hostname', dest="rigctl_hostname", default='localhost', help="Hostname or IP of the rigctld server", type=str)
    parser.add_argument('--rigctl-port', dest="rigctl_port", default=4532, help="Port for rigtctld", type=int)
    parser.add_argument('--vox', action='store_true', help="Disables rigctl", dest="vox")
    parser.add_argument('-v', action='store_true', help="Enables debug log", dest="verbose")


    group = parser.add_argument_group(title='Advanced TNC Tuneable', description="These settings can be used to tune the TNC")
    group.add_argument('--preamble-length', dest="preamble_length", default=5, help="Number of preamble frames to send", type=int)
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
        radio.tx([frame]) #TODO : this is where we could send multiple packets but a lot of protocols except one at a time
        tx_inhibit.release()

    def rf_rx_callback(packet: bytes):
        logging.debug(f"Received RF packet: {packet.hex()}")
        tnc_interface.tx(packet)
        
    if args.vox:
        rig = None
    else:
        rig = rigctl.Rigctld(hostname=args.rigctl_hostname, port=args.rigctl_port)

    if args.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    modem = freedv.FreeDV()


    tnc_interface = tnc.KissInterface(kiss_rx_callback)

    print(f'TNC port is at : {tnc_interface.ttyname}')

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

    while True:
        radio.rx()

if __name__ == "__main__":
    main()