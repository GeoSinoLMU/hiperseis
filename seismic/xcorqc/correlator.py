#!/bin/env python
"""
Description:
    Generates cross-correlations for data from staion-pairs in parallel
References:

CreationDate:   11/07/18
Developer:      rakib.hassan@ga.gov.au

Revision History:
    LastUpdate:     11/07/18   RH
    LastUpdate:     dd/mm/yyyy  Who     Optional description
"""

import os
import glob
from collections import defaultdict
from math import sqrt

import numpy as np
from scipy.spatial import cKDTree

from mpi4py import MPI
from obspy import UTCDateTime
from seismic.ASDFdatabase.FederatedASDFDataSet import FederatedASDFDataSet

import click

from seismic.ASDFdatabase.seisds import SeisDB
from seismic.xcorqc.xcorqc import IntervalStackXCorr


# define utility functions
def rtp2xyz(r, theta, phi):
    xout = np.zeros((r.shape[0], 3))
    rst = r * np.sin(theta)
    xout[:, 0] = rst * np.cos(phi)
    xout[:, 1] = rst * np.sin(phi)
    xout[:, 2] = r * np.cos(theta)
    return xout
# end func


def xyz2rtp(x, y, z):
    rout = np.zeros((x.shape[0], 3))
    tmp1 = x * x + y * y
    tmp2 = tmp1 + z * z
    rout[0] = np.sqrt(tmp2)
    rout[1] = np.arctan2(sqrt(tmp1), z)
    rout[2] = np.arctan2(y, x)
    return rout
# end func


class Dataset:
    def __init__(self, asdf_file_name, netsta_list='*'):

        self._data_path = asdf_file_name
        self._earth_radius = 6371  # km

        self.fds = FederatedASDFDataSet(asdf_file_name)
        # Gather station metadata
        netsta_list_subset = set(netsta_list.split(' ')) if netsta_list != '*' else netsta_list
        self.netsta_list = []
        self.metadata = defaultdict(list)

        rtps = []
        for netsta in self.fds.unique_coordinates.keys():
            if(netsta_list_subset != '*'):
                if netsta not in netsta_list_subset:
                    continue

            self.netsta_list.append(netsta)
            self.metadata[netsta] = self.fds.unique_coordinates[netsta]

            rtps.append([self._earth_radius,
                         np.radians(90 - self.metadata[netsta][1]),
                         np.radians(self.metadata[netsta][0])])
        # end for

        rtps = np.array(rtps)
        xyzs = rtp2xyz(rtps[:, 0], rtps[:, 1], rtps[:, 2])

        self._tree = cKDTree(xyzs)
        self._cart_location = defaultdict(list)
        for i, ns in enumerate(self.netsta_list):
            self._cart_location[ns] = xyzs[i, :]
        # end for
    # end func

    def get_closest_stations(self, netsta, other_dataset, nn=1):
        assert isinstance(netsta, basestring), 'station_name must be a string'
        assert isinstance(other_dataset, Dataset), 'other_dataset must be an instance of Dataset'
        netsta = netsta.upper()

        assert netsta in self.netsta_list, '%s not found'%(netsta)

        d, l = other_dataset._tree.query(self._cart_location[netsta], nn)

        if isinstance(l, int):
            l = [l]

        l = l[l<len(other_dataset.netsta_list)]

        if isinstance(l, int):
            l = [l]

        assert len(l), 'No stations found..'

        return list(np.array(other_dataset.netsta_list)[l])
    # end func

    def get_unique_station_pairs(self, other_dataset, nn=1):
        pairs = set()
        for st1 in self.netsta_list:
            st2list = None
            if (nn != -1):
                if self == other_dataset:
                    st2list = set(self.get_closest_stations(st1, other_dataset, nn=nn + 1))
                    if st1 in st2list:
                        st2list.remove(st1)
                    st2list = list(st2list)
                else:
                    st2list = self.get_closest_stations(st1, other_dataset, nn=nn)
            else:
                st2list = other_dataset.netsta_list
            # end if

            for st2 in st2list:
                pairs.add((st1, st2))
            # end for
        # end for

        pairs_subset = set()
        for item in pairs:
            if(item[0] == item[1]): continue

            dup_item = (item[1], item[0])
            if(dup_item not in pairs_subset and item not in pairs_subset):
                pairs_subset.add(item)
            # end if
        # end if

        return list(pairs_subset)
    # end func
# end class

def split_list(lst, npartitions):
    k, m = divmod(len(lst), npartitions)
    return [lst[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in xrange(npartitions)]
# end func

def process(data_source1, data_source2, output_path,
            interval_seconds, window_seconds,
            resample_rate=None, nearest_neighbours=1,
            fmin=0.3, fmax=1., netsta_list1='*', netsta_list2='*',
            start_time='1970-01-01T00:00:00', end_time='2100-01-01T00:00:00',
            clip_to_2std=False, whitening=False, one_bit_normalize=False, read_buffer_size=10,
            ds1_zchan=None, ds1_nchan=None, ds1_echan=None,
            ds2_zchan=None, ds2_nchan=None, ds2_echan=None,
            envelope_normalize=False, ensemble_stack=False):
    """
    DATA_SOURCE1: Text file containing paths to ASDF files \n
    DATA_SOURCE2: Text file containing paths to ASDF files \n
    OUTPUT_PATH: Output folder \n
    INTERVAL_SECONDS: Length of time window (s) over which to compute cross-correlations; e.g. 86400 for 1 day \n
    WINDOW_SECONDS: Length of stacking window (s); e.g 3600 for an hour. INTERVAL_SECONDS must be a multiple of
                    WINDOW_SECONDS; no stacking is performed if they are of the same size.
    """
    read_buffer_size *= interval_seconds
    netsta_list1 = str(netsta_list1)
    netsta_list2 = str(netsta_list2)

    comm = MPI.COMM_WORLD
    nproc = comm.Get_size()
    rank = comm.Get_rank()

    ds1 = Dataset(data_source1, netsta_list1)
    ds2 = Dataset(data_source2, netsta_list2)

    proc_stations = []
    if (rank == 0):
        def outputConfigParameters():
            # output config parameters
            fn = 'correlator.%s.cfg' % (UTCDateTime.now().strftime("%y-%m-%d.T%H.%M"))
            fn = os.path.join(output_path, fn)

            f = open(fn, 'w+')
            f.write('Parameters Values:\n\n')
            f.write('%25s\t\t: %s\n' % ('DATA_SOURCE1', data_source1))
            f.write('%25s\t\t: %s\n' % ('DATA_SOURCE2', data_source2))
            f.write('%25s\t\t: %s\n' % ('OUTPUT_PATH', output_path))
            f.write('%25s\t\t: %s\n' % ('INTERVAL_SECONDS', interval_seconds))
            f.write('%25s\t\t: %s\n\n' % ('WINDOW_SECONDS', window_seconds))

            f.write('%25s\t\t: %s\n' % ('--resample-rate', resample_rate))
            f.write('%25s\t\t: %s\n' % ('--nearest-neighbours', nearest_neighbours))
            f.write('%25s\t\t: %s\n' % ('--fmin', fmin))
            f.write('%25s\t\t: %s\n' % ('--fmax', fmax))
            f.write('%25s\t\t: %s\n' % ('--station-names1', netsta_list1))
            f.write('%25s\t\t: %s\n' % ('--station-names2', netsta_list2))
            f.write('%25s\t\t: %s\n' % ('--start-time', start_time))
            f.write('%25s\t\t: %s\n' % ('--end-time', end_time))
            f.write('%25s\t\t: %s\n' % ('--clip-to-2std', clip_to_2std))
            f.write('%25s\t\t: %s\n' % ('--one-bit-normalize', one_bit_normalize))
            f.write('%25s\t\t: %s\n' % ('--read-buffer-size', read_buffer_size))
            f.write('%25s\t\t: %s\n' % ('--envelope-normalize', envelope_normalize))

            f.close()
        # end func

        outputConfigParameters()

        pairs = ds1.get_unique_station_pairs(ds2, nn=nearest_neighbours)
        proc_stations = split_list(pairs, npartitions=nproc)
    # end if

    # broadcast workload to all procs
    proc_stations = comm.bcast(proc_stations, root=0)

    startTime = UTCDateTime(start_time)
    endTime = UTCDateTime(end_time)
    for pair in proc_stations[rank]:
        st1, st2 = pair

        x, xCorrResDict, wcResDict = IntervalStackXCorr(ds1.fds, ds2.fds, startTime,
                                                        endTime, st1, st2, ds1_zchan, ds2_zchan,
                                                        resample_rate, read_buffer_size, interval_seconds,
                                                        window_seconds, fmin, fmax, clip_to_2std, whitening,
                                                        one_bit_normalize, envelope_normalize, ensemble_stack,
                                                        output_path, 2)
    # end for
# end func


CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])

@click.command(context_settings=CONTEXT_SETTINGS)
@click.argument('data-source1',
                type=click.Path('r'))
@click.argument('data-source2',
                type=click.Path('r'))
@click.argument('output-path', required=True,
                type=click.Path(exists=True))
@click.argument('interval-seconds', required=True,
                type=int)
@click.argument('window-seconds', required=True,
                type=int)
@click.option('--resample-rate', default=None, help="Resampling rate (Hz); applies to both datasets")
@click.option('--nearest-neighbours', default=-1, help="Number of nearest neighbouring stations in data-source-2"
                                                       " to correlate against a given station in data-source-1. If"
                                                       " set to -1, correlations for a cross-product of all stations"
                                                       " in both data-sets are produced -- note, this is computationally"
                                                       " expensive.")
@click.option('--fmin', default=0.3, help="Lowest frequency for bandpass filter")
@click.option('--fmax', default=1., help="Highest frequency for bandpass filter")
@click.option('--station-names1', default='*', type=str,
              help="Station name(s) (space-delimited) to process in data-source-1; default is '*', which processes all available stations.")
@click.option('--station-names2', default='*', type=str,
              help="Station name(s) (space-delimited) to process in data-source-2; default is '*', which processes all available stations.")
@click.option('--start-time', default='1970-01-01T00:00:00',
              type=str,
              help="Date and time (in UTC format) to start from; default is year 1900.")
@click.option('--end-time', default='2100-01-01T00:00:00',
              type=str,
              help="Date and time (in UTC format) to stop at; default is year 2100.")
@click.option('--clip-to-2std', is_flag=True,
              help="Clip data in each window to +/- 2 standard deviations")
@click.option('--whitening', is_flag=True,
              help="Apply spectral whitening")
@click.option('--one-bit-normalize', is_flag=True,
              help="Apply one-bit normalization to data in each window")
@click.option('--read-buffer-size', default=10,
              type=int,
              help="Data read buffer size; default is 10 x 'interval_seconds'. This parameter allows fetching data in bulk,"
                   " which can improve efficiency, but has no effect on the results produced")
@click.option('--ds1-zchan', default='BHZ',
              type=str,
              help="Name of z-channel for data-source-1. This parameter and the ones following are only required when "
                   "channel names are ambiguous, e.g. ['HHZ', u'HYZ', u'HNZ', u'MFZ']")
@click.option('--ds1-nchan', default='BHN',
              type=str,
              help="Name of n-channel for data-source-1")
@click.option('--ds1-echan', default='BHE',
              type=str,
              help="Name of e-channel for data-source-1")
@click.option('--ds2-zchan', default='BHZ',
              type=str,
              help="Name of z-channel for data-source-2")
@click.option('--ds2-nchan', default='BHN',
              type=str,
              help="Name of n-channel for data-source-2")
@click.option('--ds2-echan', default='BHE',
              type=str,
              help="Name of e-channel for data-source-2")
@click.option('--envelope-normalize', is_flag=True, help="Envelope (via Hilbert transform) and normalize CCs. "
                                                         "This procedure is useful for clock-error detection "
                                                         "workflows")
@click.option('--ensemble-stack', is_flag=True, help="Outputs a single ensemble CC function over all data for "
                                                     "for a given station-pair. In other words, stacks over "
                                                     "'interval-seconds' are in turn stacked to produce a "
                                                     "single CC function.")
def main(data_source1, data_source2, output_path, interval_seconds, window_seconds, resample_rate,
         nearest_neighbours, fmin, fmax, station_names1, station_names2, start_time,
         end_time, clip_to_2std, whitening, one_bit_normalize, read_buffer_size,
         ds1_zchan, ds1_nchan, ds1_echan, ds2_zchan, ds2_nchan, ds2_echan, envelope_normalize,
         ensemble_stack):
    """
    DATA_SOURCE1: Path to ASDF file \n
    DATA_SOURCE2: Path to ASDF file \n
    OUTPUT_PATH: Output folder \n
    INTERVAL_SECONDS: Length of time window (s) over which to compute cross-correlations; e.g. 86400 for 1 day \n
    WINDOW_SECONDS: Length of stacking window (s); e.g 3600 for an hour. INTERVAL_SECONDS must be a multiple of
                    WINDOW_SECONDS; no stacking is performed if they are of the same size.
    """

    if(resample_rate): resample_rate = float(resample_rate)

    process(data_source1, data_source2, output_path, interval_seconds, window_seconds, resample_rate,
            nearest_neighbours, fmin, fmax, station_names1, station_names2, start_time,
            end_time, clip_to_2std, whitening, one_bit_normalize, read_buffer_size,
            ds1_zchan, ds1_nchan, ds1_echan, ds2_zchan, ds2_nchan, ds2_echan, envelope_normalize,
            ensemble_stack)
# end func

if __name__ == '__main__':
    '''
    Example call
    process("7G.refdata.h5", "7G.refdata.h5", "7G_test", 3600 * 24, 3600,
            nearest_neighbours=5,
            start_time="2013-01-01T00:00:00", end_time="2016-01-01T00:00:00",
            read_buffer_size=1,
            ds1_dec_factor=1, ds2_dec_factor=1,
            fmin=0.01, fmax=10.0,
            clip_to_2std=True,
            one_bit_normalize=True)
    '''
    main()
# end if
