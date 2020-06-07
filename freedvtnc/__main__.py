#!/usr/bin/env python3
from . import tnc, packet, rigctl, freedv, rf
import platform

# some design goals
# - configurable from command line
# - tunables for preamble, max tx time, min wait time, min wait time after tx, max allowable packet size
# - resampling
# - logging
# - SNR details
# - should be able to RX only
# - disable rig control (vox?)
# add arg parse


def kiss_rx_callback(frame: bytes):
    radio.tx(frame) #TODO : this is where we would queue up packets

def rf_rx_callback(packet: bytes):
    tnc_interface.tx(packet)

rig = rigctl.Rigctld()

#freedv_library = "libcodec2.so" if platform.uname()[0] != "Darwin" else "libcodec2.dylib"
freedv_library = "libcodec2.dylib"
modem = freedv.FreeDV(libpath=freedv_library)

tnc_interface = tnc.KissInterface(kiss_rx_callback)
radio = rf.Rf( 
                rx_device="default",
                tx_device="default",
                modem=modem,
                callback=rf_rx_callback,
                rig=rig
            )

radio.rx()

# rx thread

# kiss rx thread

# tx queue