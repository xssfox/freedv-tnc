new readme for when refactor is done.


Terms
==

FreeDV Frame - This is the data sent and received from the 700D modem. For example with the 700D modem a frame is 14 bytes. It's the smallest amount of data we deal with in the TNC.

Kiss Frame - The kiss frame, This can be very long and is typically spread over multiple freedv frames.

Packet - Within the TNC code, a packet refers to the on air packet which is typically one kiss frame (not to be confused with a FreeDV frame) and split into multiple freedv frames