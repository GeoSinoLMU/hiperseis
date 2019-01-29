#!/usr/bin/env python
"""Helper functions for converting between Pandas dataframe and FDSN Inventory,
   Network, Station and Channel objects.
"""

from collections import defaultdict
import numpy as np
import pandas as pd

from obspy import read_inventory
from obspy.core import utcdatetime
from obspy.core.inventory import Inventory, Network, Station, Channel, Site

from table_format import TABLE_SCHEMA, TABLE_COLUMNS, PANDAS_MAX_TIMESTAMP


DEFAULT_START_TIMESTAMP = pd.Timestamp("1964-1-1 00:00:00")
DEFAULT_END_TIMESTAMP = pd.Timestamp.max


def pd2Station(statcode, station_df):
    """Convert Pandas dataframe with unique station code to FDSN Station object."""
    station_data = station_df.iloc[0]
    st_start = station_data['StationStart']
    st_start = utcdatetime.UTCDateTime(st_start) if not pd.isnull(st_start) else DEFAULT_START_TIMESTAMP
    st_end = station_data['StationEnd']
    st_end = utcdatetime.UTCDateTime(st_end) if not pd.isnull(st_end) else DEFAULT_END_TIMESTAMP
    station = Station(statcode,
                      station_data['Latitude'],
                      station_data['Longitude'],
                      station_data['Elevation'],
                      start_date=st_start, creation_date=st_start,
                      end_date=st_end, termination_date=st_end,
                      site=Site(name=' '))
    for _, d in station_df.iterrows():
        ch_start = d['ChannelStart']
        ch_start = utcdatetime.UTCDateTime(ch_start) if not pd.isnull(ch_start) else None
        ch_end = d['ChannelEnd']
        ch_end = utcdatetime.UTCDateTime(ch_end) if not pd.isnull(ch_end) else None
        cha = Channel(d['ChannelCode'], '', float(d['Latitude']), float(d['Longitude']), float(d['Elevation']),
                      depth=0.0, azimuth=0.0, dip=-90.0,
                      start_date=ch_start,
                      end_date=ch_end)
        station.channels.append(cha)
    return station


def pd2Network(netcode, network_df, progressor=None):
    """Convert Pandas dataframe with unique network code to FDSN Network object."""
    net = Network(netcode, stations=[], description=' ')
    for statcode, ch_data in network_df.groupby('StationCode'):
        station = pd2Station(statcode, ch_data)
        net.stations.append(station)
        if progressor:
            progressor(len(ch_data))
    return net


def inventory2Dataframe(inv_file):
    """Load a FDSN Inventory XML file and convert entire inventory to a Pandas Dataframe.

       Returns a Pandas Dataframe with sequential integer index and sorted by [NetworkCode, StationCode].
    """
    with open(inv_file, 'r') as f:
        fdsn_inv = read_inventory(f)

    d = defaultdict(list)
    for network in fdsn_inv.networks:
        for station in network.stations:
            assert len(station.channels) > 0
            for channel in station.channels:
                d['NetworkCode'].append(network.code)
                d['StationCode'].append(station.code)
                lat = channel.latitude if channel.latitude else station.latitude
                lon = channel.longitude if channel.longitude else station.longitude
                ele = channel.elevation if channel.elevation else station.elevation
                d['Latitude'].append(lat)
                d['Longitude'].append(lon)
                d['Elevation'].append(ele)
                d['StationStart'].append(np.datetime64(station.start_date))
                d['StationEnd'].append(np.datetime64(station.end_date))
                d['ChannelCode'].append(channel.code)
                d['ChannelStart'].append(np.datetime64(channel.start_date))
                d['ChannelEnd'].append(np.datetime64(channel.end_date))
    df = pd.DataFrame.from_dict(d)
    df = df[TABLE_COLUMNS]
    df.sort_values(['NetworkCode', 'StationCode'], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df