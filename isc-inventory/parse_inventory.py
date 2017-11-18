from __future__ import absolute_import
import struct
import os
import logging
from obspy import UTCDateTime
from seismic.cluster.cluster import Station

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger()

isc_format1 = '4s12s10s8s20s20s'
isc_format2 = '4s12s10s8s20s'
isc_format3 = '5s12s10s8s20s20s'
isc_format4 = '5s12s10s8s20s'
five_char_isc_format1 = '5s11s10s8s20s20s'
five_char_isc_format2 = '5s11s10s8s20s'

"""
The first section is the data from the (ISC-)EHB station list showning station 
code, lat, lon, elev (m) and then the first and last dates the coordinates 
were used. No last date implies ongoing use.

The second section is our map from the code to FDSN where available, showing 
sta, network, location, channel and open date, close date and if we have it 
the sampling rates.
"""

isc_file_1 = os.path.join('isc-inventory', 'ehb.stn')
isc_file_2 = os.path.join('isc-inventory', 'iscehb.stn')


def gather_isc_stations():
    sta_dict = {}
    # don't change file order, ehb.stn should be older stations list and we
    # keep the more modern ones in the dict
    for f in [isc_file_1, isc_file_2]:
        sta_dict = _read_sta_file(f, sta_dict)
    return sta_dict


def _read_sta_file(f, sta_dict):
    with open(f, 'r') as sta:
        stations = 0
        for l, line in enumerate(sta):
            line = line.rstrip()
            line_length = len(line)
            if line_length in [74, 54, 75, 55]:
                if line_length == 74:
                    isc_format = isc_format1
                elif line_length == 54:
                    isc_format = isc_format2
                elif line_length == 75:
                    isc_format = isc_format3
                else:  # line_length == 55
                    isc_format = isc_format4
                sta_tuple = struct.unpack(isc_format, line.encode())

                # only select for valid station names
                if sta_tuple[0].strip():
                    sta = sta_tuple[0].strip()
                    stations += 1
                    log.debug('found station {}, station_code: {}, '
                              'sta details: {}'.format(stations, sta_tuple[0],
                                                       sta_tuple))
                    try:
                        if len(sta_tuple) == 5:
                            lat, lon, ele, start_date = sta_tuple[1:5]
                            end_date = False
                        elif len(sta_tuple) == 6:
                            lat, lon, ele, start_date, end_date = \
                                sta_tuple[1:6]
                        else:  # no other formats should sneak in
                            raise Exception('We must have tuple of length'
                                            '5 or 6')
                        # check for conversion problems imply we need to use
                        # alternative formats
                        _ = float(lat.decode())
                        _ = float(lon.decode())
                        _ = float(ele.decode())
                        start_date = UTCDateTime(start_date.decode())
                        log.debug(
                            'Station: {sta} start date: {start_date}'.format(
                                sta=sta, start_date=start_date))
                        if end_date:
                            end_date = UTCDateTime(end_date)
                            log.debug(
                                'Station: {sta} end date: {end_date}'.format(
                                    sta=sta, end_date=end_date))
                    # exception handling due to 5 char station names
                    except ValueError:
                        log.debug(sta_tuple)
                        if line_length == 54:
                            sta_tuple = struct.unpack(five_char_isc_format2,
                                                      line.encode())
                            log.debug(sta_tuple)
                        elif line_length == 74:
                            sta_tuple = struct.unpack(five_char_isc_format1,
                                                      line.encode())
                            log.debug(sta_tuple)
                        else:
                            raise

                    if sta_tuple[0] in sta_dict:
                        log.warning('This station already exists in the'
                                    'stations dict. '
                                    'Overwriting with newer station '
                                    'with same station code')
                    sta_dict[sta_tuple[0]] = Station(
                        station_code=sta_tuple[0],
                        latitude=float(sta_tuple[1]),
                        longitude=float(sta_tuple[2]),
                        elevation=float(sta_tuple[3]),
                        network_code='dummy')

            else:
                # make sure we know all other formats before we ignore them
                assert line_length == 80 or line_length == 86 or \
                       line_length == 92
    return sta_dict


if __name__ == "__main__":
    gather_isc_stations()
