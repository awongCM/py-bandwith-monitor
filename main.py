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

# Constants go here
SLEEP_INTERVAL = 1


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
    '''
    Section to fetch all the network interfaces attached to this device
    '''
    stats = psutil.net_if_stats()

    io_counters_tuple = psutil.net_io_counters(pernic=True)

    for nic in io_counters_tuple:
        print("{}: ".format(nic))
        st = stats[nic]
        print_nic_info(st)

        io = io_counters_tuple[nic]
        print_io_stats(io)


def get_simple_network_monitor():
    '''
    Section to get simple network monitor (which runs in indefinite state)
    '''
    old_value = 0
    while True:
        new_value = psutil.net_io_counters().bytes_sent + \
            psutil.net_io_counters().bytes_recv

        if old_value:
            send_stat(new_value - old_value)

        old_value = new_value
        time.sleep(1)


def convert_to_gbit(value):
    return value/1024./1024./1024.*8


def send_stat(value):
    print("{}".format(convert_to_gbit(value)))


def get_some_network_polling():
    '''
    TODOS
    '''
    print('i am going to fetch network polling here ')
    pass


def refresh_polling():
    '''
    TODOS
    '''
    print('then i am expected to do refresh polling interval straight ater')
    pass


def display_live_network_monitoring():
    '''
    TODOS
    '''
    try:
        while True:
            get_some_network_polling()
            refresh_polling()
            time.sleep(SLEEP_INTERVAL)

    except (KeyboardInterrupt, SystemExit):
        print('until a certain someone or something killed it...')
        pass


def main():
    # use this
    get_network_monitor_stats()

    # or this
    # get_simple_network_monitor()

    # or even better
    display_live_network_monitoring()


if __name__ == "__main__":
    main()
