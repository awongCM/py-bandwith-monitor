import time
import psutil
import os
from psutil._common import bytes2human

# Duplex dcitionary map
duplex_map = {
    psutil.NIC_DUPLEX_FULL: "full",
    psutil.NIC_DUPLEX_HALF: "half",
    psutil.NIC_DUPLEX_UNKNOWN: "?",
}


def print_nic_info(nic):
    print("    stats       : ", end='')
    print("speed=%sMB, duplex=%s, mtu=%s, up=%s" % (
        nic.speed, duplex_map[nic.duplex], nic.mtu,
        "yes" if nic.isup else "no"))


def print_io_stats(io):
    print("    incoming       : ", end='')
    print("bytes={}, pkts={}, errs={}, drops={}".format(
        bytes2human(io.bytes_recv), io.packets_recv, io.errin,
        io.dropin))
    print("    outgoing       : ", end='')
    print("bytes={}, pkts={}, errs={}, drops={}".format(
        bytes2human(io.bytes_sent), io.packets_sent, io.errout,
        io.dropout))


def get_network_monitor_stats():

    stats = psutil.net_if_stats()

    io_counters_tuple = psutil.net_io_counters(pernic=True)

    for nic in io_counters_tuple:
        print("{}: ".format(nic))
        st = stats[nic]
        print_nic_info(st)

        io = io_counters_tuple[nic]
        print_io_stats(io)


def main():
    get_network_monitor_stats()


main()
