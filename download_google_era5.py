#!/usr/bin/env python3
"""Anonymous ARCO ERA5 L137 regional extraction, resumable hourly NetCDF.

Uses HTTP Range requests into independent Blosc1 blocks. No global arrays
are downloaded or allocated. See README.md for configuration and limitations.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import struct
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
if (ROOT / '.runtime_deps').exists():
    sys.path.insert(0, str(ROOT / '.runtime_deps'))
import numpy as np
import requests
from numcodecs import Blosc
from netCDF4 import Dataset, date2num

BASE = 'https://storage.googleapis.com/gcp-public-data-arco-era5/ar/'
ML = BASE + 'model-level-1h-0p25deg.zarr-v1/'
SFC = BASE + 'full_37-1h-0p25deg-chunk-1.zarr-v3/'
ALIASES = {
    'u': 'u_component_of_wind', 'v': 'v_component_of_wind',
    't': 'temperature', 'q': 'specific_humidity', 'w': 'vertical_velocity',
    'o3': 'ozone_mass_mixing_ratio', 'd': 'divergence',
    'vo': 'vorticity', 'cc': 'fraction_of_cloud_cover',
    'clwc': 'specific_cloud_liquid_water_content',
    'ciwc': 'specific_cloud_ice_water_content',
    'crwc': 'specific_rain_water_content', 'cswc': 'specific_snow_water_content',
}


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    for attempt in range(10):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.02)


class DownloadStopped(Exception):
    """User requested a clean exit, retaining all partial ranges."""


class DownloadControl:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock = threading.Lock()
        self.mode = 'run'
        self.checked = 0
        self.aborted = threading.Event()

    def state(self, force=False):
        with self.lock:
            if self.path and (force or time.monotonic() - self.checked >= .1):
                self.checked = time.monotonic()
                try:
                    mode = json.loads(self.path.read_text(encoding='utf-8-sig')).get('command')
                    if mode in ('run', 'pause', 'stop'):
                        self.mode = mode
                except (OSError, ValueError):
                    pass
            return self.mode

    def wait(self, delay=0):
        deadline = time.monotonic() + delay
        while True:
            mode = self.state()
            if self.aborted.is_set() or mode == 'stop':
                raise DownloadStopped('Download interrupted; resumable cache retained')
            remaining = deadline - time.monotonic()
            if mode != 'pause' and remaining <= 0:
                return
            self.aborted.wait(.1 if mode == 'pause' else min(.1, remaining))


class RetryableRangeError(IOError):
    """The remote object changed or its transfer ended early."""


class HTTP:
    def __init__(self, cfg, control=None, auto_reconnect=False):
        self.cfg = cfg
        self.local = threading.local()
        self.lock = threading.Lock()
        self.bytes = 0
        self.requests = 0
        self.cache_hits = 0
        self.cached_bytes = 0
        self.resumed_requests = 0
        self.cache_dir = None
        self.progress_callback = None
        self.last_progress = 0
        self.control = control or DownloadControl()
        self.hour_bytes = {}
        self.auto_reconnect = auto_reconnect
        self.last_retry_log = float('-inf')

    def get(self, url, first=None, last=None):
        self.control.wait()
        if not hasattr(self.local, 'session'):
            self.local.session = requests.Session()
        final = part = meta_path = None
        expected_size = last - first + 1 if first is not None else None
        cache_dir = getattr(self.local, 'cache_dir', self.cache_dir)
        if cache_dir is not None and first is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256(f'{url}|{first}|{last}'.encode()).hexdigest()
            final = cache_dir / (key + '.bin')
            part = cache_dir / (key + '.part')
            meta_path = cache_dir / (key + '.json')
            try:
                saved = json.loads(meta_path.read_text(encoding='utf-8'))
                if final.exists():
                    data = final.read_bytes()
                    if len(data) == expected_size and hashlib.sha256(data).hexdigest() == saved.get('sha256'):
                        with self.lock:
                            self.cache_hits += 1
                            self.cached_bytes += len(data)
                        return data
                    final.unlink()
            except (OSError, ValueError):
                pass
        attempt = 0
        while True:
            self.control.wait()
            try:
                offset, etag = 0, None
                if part is not None and part.exists():
                    try:
                        info = json.loads(meta_path.read_text(encoding='utf-8'))
                        etag = info.get('etag')
                        if etag and info.get('url') == url and info.get('first') == first and info.get('last') == last:
                            offset = part.stat().st_size
                        if offset > expected_size or not etag:
                            offset = 0
                    except (OSError, ValueError):
                        offset = 0
                    if not offset:
                        part.write_bytes(b'')
                # A complete .part may remain if shutdown occurred before rename.
                if offset == expected_size and part is not None:
                    data = part.read_bytes()
                else:
                    headers = {'Accept-Encoding': 'identity'}
                    if first is not None:
                        headers['Range'] = f'bytes={first + offset}-{last}'
                        if offset:
                            headers['If-Match'] = etag
                    with self.local.session.get(url, headers=headers,
                            timeout=self.cfg['timeout_seconds'], stream=True) as r:
                        with self.lock:
                            self.requests += 1
                            self.resumed_requests += bool(offset)
                        if r.status_code == 412 and part is not None:
                            part.write_bytes(b'')
                            raise RetryableRangeError('Source ETag changed; restarting this range')
                        r.raise_for_status()
                        if first is not None:
                            expected = f'bytes {first + offset}-{last}/'
                            if r.status_code != 206 or not r.headers.get('Content-Range', '').startswith(expected):
                                raise RuntimeError('Server did not honor byte range; refusing global download')
                        if part is not None:
                            atomic_json(meta_path, dict(url=url, first=first, last=last,
                                        etag=r.headers.get('ETag')))
                            with part.open('ab' if offset else 'wb') as stream:
                                for block in r.iter_content(chunk_size=65536):
                                    self.control.wait()
                                    stream.write(block)
                                    stream.flush()
                                    self._received(len(block))
                                os.fsync(stream.fileno())
                            data = part.read_bytes()
                        else:
                            data = r.content
                            self._received(len(data))
                if first is not None and len(data) != expected_size:
                    raise RetryableRangeError('Truncated byte range; partial bytes retained for resume')
                if part is not None:
                    info = json.loads(meta_path.read_text(encoding='utf-8'))
                    info['sha256'] = hashlib.sha256(data).hexdigest()
                    atomic_json(meta_path, info)
                    part.replace(final)
                return data
            except (requests.RequestException, RetryableRangeError) as exc:
                if isinstance(exc, requests.HTTPError):
                    status = exc.response.status_code if exc.response is not None else 0
                    transient = status in (408, 429) or 500 <= status < 600
                else:
                    transient = isinstance(exc, (
                        requests.Timeout, requests.ConnectionError,
                        requests.exceptions.ChunkedEncodingError, RetryableRangeError,
                    )) and not isinstance(exc, requests.exceptions.SSLError)
                if not transient:
                    raise

                if attempt < self.cfg['retries']:
                    delay = min(2 ** min(attempt, 4), 10)
                elif not self.auto_reconnect:
                    raise
                else:
                    delay = min(15 * 2 ** min(attempt - self.cfg['retries'], 4), 120)
                    with self.lock:
                        now = time.monotonic()
                        if now - self.last_retry_log >= 15:
                            self.last_retry_log = now
                            print(f'连接暂时中断，{delay} 秒后自动重试：{exc}', flush=True)

                # Drop pooled connections and retry only this request. Its
                # .part and ETag are read again on the next attempt.
                self.local.session.close()
                self.control.wait(delay)
                attempt += 1

    def _received(self, count):
        with self.lock:
            self.bytes += count
            hour = getattr(self.local, 'hour', None)
            if hour:
                self.hour_bytes[hour] = self.hour_bytes.get(hour, 0) + count
            if self.progress_callback and time.monotonic() - self.last_progress >= 2:
                self.last_progress = time.monotonic()
                self.progress_callback()


def decode_blocks(header, table, fetch, flat_indices, dtype):
    """Decode only selected Blosc1 blocks; offsets may be physically unordered."""
    version, codec, flags, typesize, nbytes, blocksize, cbytes = struct.unpack('<BBBBIII', header)
    if version != 2 or not blocksize or flags & 4:
        raise ValueError('Unsupported Blosc layout (requires Blosc1 without bitshuffle)')
    dtype = np.dtype(dtype)
    byte_indices = flat_indices * dtype.itemsize
    out = np.empty(len(flat_indices), dtype=dtype)
    if flags & 2:  # BLOSC_MEMCPYED: data starts after the 16-byte header
        for block in np.unique(byte_indices // blocksize):
            mask = byte_indices // blocksize == block
            start = int(block) * blocksize
            raw = fetch(16 + start, 16 + min(start + blocksize, nbytes) - 1)
            arr = np.frombuffer(raw, dtype=dtype)
            out[mask] = arr[(byte_indices[mask] - start) // dtype.itemsize]
        return out
    count = (nbytes + blocksize - 1) // blocksize
    offsets = np.frombuffer(table, dtype='<u4', count=count)
    ordered = sorted(int(v) for v in offsets) + [cbytes]
    ends = dict(zip(ordered[:-1], ordered[1:]))
    for block in np.unique(byte_indices // blocksize):
        block = int(block)
        start = int(offsets[block])
        payload = fetch(start, ends[start] - 1)
        size = min(blocksize, nbytes - block * blocksize)
        # A valid one-block Blosc buffer preserves shuffle/compressor flags.
        # Blosc encodes the final short block without byte-stream splitting.
        single_flags = flags | 16 if size < blocksize else flags
        single = struct.pack('<BBBBIII', version, codec, single_flags, typesize,
                             size, size, 20 + len(payload))
        raw = Blosc().decode(single + struct.pack('<I', 20) + payload)
        if len(raw) != size:
            raise IOError('Unexpected decoded block length')
        arr = np.frombuffer(raw, dtype=dtype)
        mask = byte_indices // blocksize == block
        out[mask] = arr[(byte_indices[mask] - block * blocksize) // dtype.itemsize]
    return out


class Store:
    def __init__(self, http, url):
        self.http, self.url = http, url
        self.meta = json.loads(http.get(url + '.zmetadata'))['metadata']

    def spec(self, var):
        return self.meta[var + '/.zarray']

    def attrs(self, var):
        return self.meta[var + '/.zattrs']

    def coord(self, var):
        spec = self.spec(var)
        if len(spec['shape']) != 1 or spec['chunks'][0] < spec['shape'][0]:
            raise ValueError('Coordinate is not stored as a single chunk')
        data = self.http.get(self.url + var + '/0')
        raw = Blosc().decode(data) if spec['compressor'] else data
        return np.frombuffer(raw, dtype=spec['dtype'])[:spec['shape'][0]]

    def time_index(self, stamp):
        attrs = self.attrs('time')
        value = date2num(stamp, attrs['units'], attrs.get('calendar', 'standard'))
        # Read the relevant time chunk: do not assume time index == hours since 1900.
        spec = self.spec('time')
        # ARCO starts at 1900-01-01, but check this from the coordinate itself.
        raw = self.http.get(self.url + 'time/0')
        decoded = Blosc().decode(raw) if spec['compressor'] else raw
        initial = np.frombuffer(decoded, dtype=spec['dtype'])
        step = initial[1] - initial[0]
        index = int(round(float((value - initial[0]) / step)))
        if not 0 <= index < spec['shape'][0]:
            raise ValueError('Time outside source array')
        chunk = index // spec['chunks'][0]
        if chunk:
            raw = self.http.get(self.url + f'time/{chunk}')
            decoded = Blosc().decode(raw) if spec['compressor'] else raw
            initial = np.frombuffer(decoded, dtype=spec['dtype'])
        if initial[index % spec['chunks'][0]] != value:
            raise ValueError('Source does not contain the exact requested hour')
        return index

    def read(self, var, chunk_coords, indices):
        spec = self.spec(var)
        comp = spec['compressor']
        if spec['order'] != 'C' or spec['filters'] or not comp or comp['id'] != 'blosc':
            raise ValueError('Range reader requires C-order Blosc without Zarr filters')
        sep = spec.get('dimension_separator', '.')
        url = self.url + var + '/' + sep.join(map(str, chunk_coords))
        header = self.http.get(url, 0, 15)
        _, _, flags, _, nbytes, blocksize, _ = struct.unpack('<BBBBIII', header)
        count = (nbytes + blocksize - 1) // blocksize
        table = b'' if flags & 2 else self.http.get(url, 16, 16 + 4 * count - 1)
        return decode_blocks(header, table,
                lambda a, b: self.http.get(url, a, b), indices, spec['dtype'])


def selection(cfg, lat, lon):
    if cfg['spatial_mode'] == 'point':
        latitude = float(cfg['latitude'])
        longitude = float(cfg['longitude'])
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 360:
            raise ValueError('Invalid point coordinates')
        ilat = np.array([int(np.argmin(abs(lat - latitude)))])
        delta = abs((lon - longitude + 180) % 360 - 180)
        ilon = np.array([int(np.argmin(delta))])
    elif cfg['spatial_mode'] == 'bbox':
        south, north = cfg['south'], cfg['north']
        if not -90 <= south <= north <= 90:
            raise ValueError('Require -90 <= south <= north <= 90')
        ilat = np.flatnonzero((lat >= south) & (lat <= north))
        west, east = cfg['west'], cfg['east']
        if not (-180 <= west <= 360 and -180 <= east <= 360):
            raise ValueError('Longitude outside [-180, 360]')
        if abs(east - west) >= 360:
            ilon = np.arange(len(lon))
        else:
            w, e = west % 360, east % 360
            ilon = np.flatnonzero((lon >= w) & (lon <= e) if w <= e else (lon >= w) | (lon <= e))
    else:
        raise ValueError('spatial_mode must be point or bbox')
    if not len(ilat) or not len(ilon):
        raise ValueError('No grid cells in bbox; enlarge it or use point mode')
    return ilat, ilon


def make_tasks(store, var, tidx, lev, iy, ix):
    dims = store.attrs(var)['_ARRAY_DIMENSIONS']
    expected = ['time', 'hybrid', 'latitude', 'longitude'] if lev is not None else ['time', 'latitude', 'longitude']
    if dims != expected:
        raise ValueError(f'Unexpected dimensions for {var}: {dims}')
    axes = [np.array([tidx]), lev - 1, iy, ix] if lev is not None else [np.array([tidx]), iy, ix]
    chunks = store.spec(var)['chunks']
    # Group output cells by source chunk; supports spatially rechunked stores too.
    coords = np.meshgrid(*axes, indexing='ij')
    positions = np.arange(coords[0].size)
    cells = np.stack([a.ravel() for a in coords], axis=1)
    groups = cells // np.array(chunks)
    keys, inv = np.unique(groups, axis=0, return_inverse=True)
    for k, key in enumerate(keys):
        mask = inv == k
        local = cells[mask] % np.array(chunks)
        flat = np.ravel_multi_index(local.T, tuple(chunks))
        yield tuple(key.tolist()), positions[mask], flat, tuple(len(a) for a in axes)


def write_hour(path, stamp, arrays, attributes, lat, lon, levels, cfg, signature):
    temp = path.with_suffix('.nc.partial')
    # Windows netCDF-C builds may fail on Chinese paths. Python handles the
    # actual file access; netCDF-C writes an in-memory NetCDF4 image instead.
    ds = Dataset('output.nc', 'w', memory=1024)
    try:
        for dim, size in [('time', 1), ('level', len(levels)), ('latitude', len(lat)), ('longitude', len(lon))]:
            ds.createDimension(dim, size)
        tc = ds.createVariable('time', 'f8', ('time',))
        tc.units = 'hours since 1900-01-01 00:00:00'
        tc.calendar = 'proleptic_gregorian'
        tc[:] = date2num(stamp, tc.units, tc.calendar)
        for name, vals, unit in [('level', levels, '1'), ('latitude', lat, 'degrees_north'), ('longitude', lon, 'degrees_east')]:
            v = ds.createVariable(name, 'i4' if name == 'level' else 'f4', (name,))
            v[:] = vals
            v.units = unit
        ds['level'].long_name = 'ERA5 model level: 1=top, 137=bottom'
        for name, data in arrays.items():
            dims = ('time', 'level', 'latitude', 'longitude') if data.ndim == 4 else ('time', 'latitude', 'longitude')
            v = ds.createVariable(name, 'f4', dims, zlib=True, complevel=4)
            v[:] = data
            for attr, value in attributes[name].items():
                if attr != '_ARRAY_DIMENSIONS' and isinstance(value, (str, int, float)):
                    v.setncattr(attr, value)
        ds.source_model_levels = ML
        ds.source_surface = SFC
        ds.site = cfg['site']
        ds.config_signature = signature
        ds.request_config = json.dumps(cfg, ensure_ascii=False)
        ds.height_inputs_note = 't,q on model levels; z is SURFACE geopotential; lnsp=log(sp/1Pa)'
        ds.Conventions = 'CF-1.8'
        image = ds.close()
    except BaseException:
        if ds.isopen():
            ds.close()
        raise
    temp.write_bytes(image)
    temp.replace(path)


def open_nc(path):
    """Read a NetCDF file through Python to support Unicode Windows paths."""
    return Dataset('input.nc', memory=Path(path).read_bytes())


def zone_offset(name):
    if name == 'UTC':
        return timedelta(0)
    if name in ('beijing', 'Asia/Shanghai'):
        return timedelta(hours=8)
    raise ValueError('Time zone must be UTC, beijing or Asia/Shanghai')


def selected_times(cfg):
    """Return UTC hours. beijing applies to dates AND hour filtering.

    Historical UTC/Asia-Shanghai configs keep their UTC-hour semantics;
    hours_time_zone can explicitly override the hour filter's time zone.
    """
    offset = zone_offset(cfg['time_zone'])
    hours_zone = cfg.get('hours_time_zone', 'beijing' if cfg['time_zone'] == 'beijing' else 'UTC')
    hour_offset = zone_offset(hours_zone)
    start, end = (datetime.fromisoformat(cfg[k]) for k in ('start', 'end'))
    if any(t.tzinfo or t.minute or t.second or t.microsecond for t in (start, end)) or start > end:
        raise ValueError('Use naive ISO timestamps on exact hours, start <= end')
    start, end = start - offset, end - offset
    hours = cfg['hours']
    if not isinstance(hours, list) or not hours or any(type(h) is not int or not 0 <= h <= 23 for h in hours):
        raise ValueError('hours must be a nonempty list of integers 0..23')
    result = []
    stamp = start
    while stamp <= end:
        if (stamp + hour_offset).hour in hours:
            result.append(stamp)
        stamp += timedelta(hours=1)
    if not result:
        raise ValueError('No selected hours')
    return result


def request_folder(cfg):
    """Reuse legacy downloads when only network/concurrency settings change."""
    tuning = {'workers', 'parallel_hours', 'timeout_seconds', 'retries', 'generate_real_height'}
    def identity(config):
        result = {k: v for k, v in config.items() if k not in tuning}
        # Editing unused coordinates in the visual form must not cause a
        # second download of identical selected data.
        if config.get('spatial_mode','point')=='point':
            for key in ('north','south','west','east'):result.pop(key,None)
        else:
            for key in ('latitude','longitude'):result.pop(key,None)
        # Compare the same UTC selection across different input time zones,
        # so switching presentation units does not discard existing progress.
        if all(k in config for k in ('time_zone', 'start', 'end', 'hours')):
            times = selected_times(config)
            for key in ('time_zone', 'hours_time_zone', 'start', 'end', 'hours'):
                result.pop(key, None)
            result['selected_utc_hours'] = [t.isoformat() for t in times]
        return result
    root = Path(cfg['output_dir'])
    if not root.is_absolute():
        root = ROOT / root
    matches = []
    for candidate in root.glob('*/request.json'):
        try:
            saved = json.loads(candidate.read_text(encoding='utf-8'))
            if identity(saved['config']) == identity(cfg):
                matches.append((len(list(candidate.parent.glob('*.nc'))), candidate.parent, saved['signature']))
        except (OSError, KeyError, ValueError):
            continue
    if matches:
        _, folder, signature = max(matches, key=lambda item: item[0])
        return folder, signature
    signature = hashlib.sha256(json.dumps(identity(cfg), sort_keys=True).encode()).hexdigest()[:16]
    site = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(cfg['site'])).strip(' .')
    if not site or site.upper() in ('CON', 'PRN', 'AUX', 'NUL') or re.fullmatch(r'(COM|LPT)[1-9]', site, re.I):
        site = 'site_' + site
    if 'start' not in cfg or 'end' not in cfg:
        return root / (site + '_' + signature), signature
    period = datetime.fromisoformat(cfg['start']).strftime('%Y-%m-%d') + '_' + datetime.fromisoformat(cfg['end']).strftime('%Y-%m-%d')
    preferred = root / (site + '_' + period)
    if preferred.exists():
        preferred = root / (site + '_' + period + '_' + signature[:8])
    return preferred, signature


def read_cached_task(http, cache, store, var, key, indices):
    # Each worker sees the cache for its own hour even while other hours run.
    http.local.cache_dir = cache / 'ranges'
    http.local.hour = cache.name
    try:
        return store.read(var, key, indices)
    finally:
        del http.local.cache_dir
        del http.local.hour


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=ROOT / 'config.json')
    p.add_argument('--max-hours', type=int, default=0, help='0=all missing hours')
    p.add_argument('--time-budget', type=float, default=0, help='Stop between hours after this many seconds; 0=unlimited')
    p.add_argument('--plan', action='store_true', help='Read metadata/coordinates and show plan only')
    p.add_argument('--status-file', type=Path, help='Additional live status file for the desktop window')
    p.add_argument('--control-file', type=Path, help='JSON command: run, pause or stop')
    args = p.parse_args(argv)
    cfg = json.loads(args.config.read_text(encoding='utf-8-sig'))
    parallel_hours = cfg.get('parallel_hours', 1)
    if type(parallel_hours) is not int or not 1 <= parallel_hours <= 16:
        raise ValueError('parallel_hours must be an integer from 1 to 16')
    times = selected_times(cfg)
    if not 1 <= cfg['workers'] <= 64 or cfg['timeout_seconds'] <= 0 or cfg['retries'] < 0:
        raise ValueError('Invalid network settings')
    lev = np.arange(1, 138) if cfg['levels'] == 'all' else np.array(cfg['levels'])
    if lev.ndim != 1 or not len(lev) or not np.issubdtype(lev.dtype, np.integer) or len(set(lev)) != len(lev) or np.any((lev < 1) | (lev > 137)):
        raise ValueError('levels must be all or unique integers 1..137')
    names = list(dict.fromkeys(cfg['variables']))
    if not names or any(n not in ALIASES for n in names):
        raise ValueError(f'variables must use: {list(ALIASES)}')
    if cfg['include_height_inputs']:
        if not np.array_equal(lev, np.arange(1, 138)):
            raise ValueError('Height inputs require all 137 levels, ordered from 1 to 137')
        names = list(dict.fromkeys(names + ['t', 'q']))
    control = DownloadControl(args.control_file)
    http = HTTP(cfg, control, auto_reconnect=not args.plan)
    ml = Store(http, ML)
    sfc = Store(http, SFC) if cfg['include_height_inputs'] else None
    lat, lon = ml.coord('latitude'), ml.coord('longitude')
    iy, ix = selection(cfg, lat, lon)
    sources = [(n, ml, ALIASES[n], lev) for n in names]
    if sfc:
        if not np.array_equal(lat, sfc.coord('latitude')) or not np.array_equal(lon, sfc.coord('longitude')):
            raise ValueError('Model/surface grids do not match')
        sources += [('sp', sfc, 'surface_pressure', None), ('z', sfc, 'geopotential_at_surface', None)]
    time_base = {}
    for store in [ml] + ([sfc] if sfc else []):
        attrs = store.meta['.zattrs']
        valid_start = datetime.fromisoformat(attrs['valid_time_start'].replace('Z', '+00:00')).replace(tzinfo=None)
        stop_text = attrs['valid_time_stop']
        valid_stop = datetime.fromisoformat(stop_text.replace('Z', '+00:00')).replace(tzinfo=None)
        if len(stop_text) == 10:
            valid_stop += timedelta(hours=23)
        if times[0] < valid_start or times[-1] > valid_stop:
            raise ValueError(f'Request outside stable ERA5 range: {attrs}')
        time_base[store.url] = store.time_index(times[0])
    folder, signature = request_folder(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    # One writer per request, including when a background copy is running.
    request_lock = open(folder / '.download.lock', 'a+b')
    request_lock.seek(0)
    if os.name == 'nt':
        import msvcrt
        try:
            msvcrt.locking(request_lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            request_lock.close()
            raise RuntimeError('This request already has an active downloader') from exc
    else:
        import fcntl
        fcntl.flock(request_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    plan = dict(config=cfg, signature=signature, requested_hours=len(times),
                selected_latitudes=lat[iy].tolist(), selected_longitudes=lon[ix].tolist(),
                selected_levels=lev.tolist(), variables=[n for n, *_ in sources],
                source_metadata={s.url: s.meta['.zattrs'] for s in [ml] + ([sfc] if sfc else [])},
                output_dir=str(folder), note='One hourly file is committed only when all variables are complete')
    (folder / 'request.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: plan[k] for k in ('requested_hours', 'selected_latitudes', 'selected_longitudes', 'variables', 'output_dir')}, ensure_ascii=False), flush=True)
    if args.plan:
        return
    start_clock = time.monotonic()
    complete = []
    downloaded = 0
    error = None
    current_hour = None
    running_hours = {}
    active = True
    records_path = folder / 'download_records.json'
    try:
        records = json.loads(records_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        records = {'signature': signature, 'hours': {}}
    if records.get('signature') != signature:
        raise ValueError('Download record signature mismatch')

    def event(kind, **details):
        row = dict(event=kind, utc=datetime.now(timezone.utc).isoformat(), **details)
        with (folder / 'download_history.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())

    def check_file(path, stamp):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        previous = records['hours'].get(path.stem, {})
        if previous.get('sha256') and previous['sha256'] != digest:
            raise ValueError('File checksum mismatch')
        with open_nc(path) as ds:
            if ds.config_signature != signature or ds['time'][0] != date2num(stamp, ds['time'].units, ds['time'].calendar):
                raise ValueError('File request/time mismatch')
            for dim, size in [('time', 1), ('level', len(lev)), ('latitude', len(iy)), ('longitude', len(ix))]:
                if len(ds.dimensions[dim]) != size:
                    raise ValueError(f'Wrong dimension: {dim}')
            for name in [n for n, *_ in sources] + (['lnsp'] if sfc else []):
                data = ds[name][:]
                if np.ma.getmaskarray(data).any() or not np.isfinite(data).all():
                    raise ValueError(f'Incomplete variable: {name}')
        return dict(state='complete', file=path.name, bytes=path.stat().st_size, sha256=digest)

    # Rebuild the manifest from disk; an interrupted record write never loses
    # an already committed hour. Corrupt outputs are preserved and redownloaded.
    for stamp in times:
        key = f'{stamp:%Y%m%d_%H}'
        path = folder / (key + '.nc')
        if path.exists():
            try:
                records['hours'][key] = check_file(path, stamp)
                complete.append(path.name)
            except Exception as exc:
                quarantine = path.with_name(path.name + '.corrupt-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
                path.replace(quarantine)
                records['hours'][key] = dict(state='pending', error=str(exc), preserved_as=quarantine.name)
                event('corrupt_output', hour=key, reason=str(exc), preserved_as=quarantine.name)
        else:
            records['hours'][key] = dict(state='pending', file=path.name)
    atomic_json(records_path, records)
    event('run_started', completed_hours=len(complete), requested_hours=len(times))
    timing_path = folder / 'timing.json'
    try:
        timing = json.loads(timing_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        timing = dict(elapsed_active_seconds=0., paused_seconds=0., baseline_hours=len(complete))
    last_tick = time.monotonic()
    last_mode = control.state()

    def save_status():
        nonlocal last_tick, last_mode
        now = time.monotonic()
        mode = control.state()
        key = 'paused_seconds' if last_mode == 'pause' else 'elapsed_active_seconds'
        timing[key] = timing.get(key, 0.) + now - last_tick
        last_tick, last_mode = now, mode
        atomic_json(timing_path, timing)
        fractions = sum((s['total_tasks'] - s['remaining']) / max(1, s['total_tasks']) for s in running_hours.values())
        units = max(0, len(complete) - timing['baseline_hours']) + fractions
        seconds_per_hour = timing['elapsed_active_seconds'] / units if units >= .5 and timing['elapsed_active_seconds'] >= 10 else None
        eta = max(0., len(times) - len(complete) - fractions) * seconds_per_hour if seconds_per_hour is not None else None
        status = dict(requested_hours=len(times), completed_hours=len(complete),
                      newly_downloaded_hours=downloaded, complete=len(complete) == len(times),
                      run_state='complete' if len(complete) == len(times) else ('running' if active else 'stopped'),
                      process_id=os.getpid(),
                      network_bytes_this_run=http.bytes, requests_this_run=http.requests,
                      cache_hits_this_run=http.cache_hits,
                      reused_cache_bytes_this_run=http.cached_bytes,
                      resumed_ranges_this_run=http.resumed_requests,
                      current_hour_utc=min(running_hours) if running_hours else current_hour,
                      active_hours_utc=sorted(running_hours),
                      active_file_progress=[dict(hour=hour, file=hour + '.nc',
                          chunks_total=state['total_tasks'],
                          chunks_complete=state['total_tasks'] - state['remaining'],
                          percent=round(100 * (state['total_tasks'] - state['remaining']) / max(1, state['total_tasks']), 1),
                          network_bytes=http.hour_bytes.get(hour, 0)) for hour, state in sorted(running_hours.items())],
                      data_folder=str(folder),
                      parallel_hours=parallel_hours, workers=cfg["workers"],
                      elapsed_seconds=time.monotonic() - start_clock,
                      elapsed_active_seconds=timing['elapsed_active_seconds'],
                      paused_seconds=timing.get('paused_seconds', 0.),
                      eta_seconds=eta, seconds_per_hour=seconds_per_hour,
                      completed_files=complete, error=error,
                      updated_utc=datetime.now(timezone.utc).isoformat())
        if active and control.state() == 'pause':
            status['run_state'] = 'paused'
        atomic_json(folder / 'status.json', status)
        if args.status_file:
            atomic_json(args.status_file, status)
        return status

    # Only the coordinator writes NetCDF, manifests and progress. Network
    # workers never call netCDF-C (it is not safe to use concurrently).
    save_status()
    pending = iter(stamp for stamp in times if f'{stamp:%Y%m%d_%H}.nc' not in complete)
    submitted_hours = 0
    exhausted = False
    try:
        with ThreadPoolExecutor(max_workers=cfg['workers']) as pool, ExitStack() as cleanup:
            # Wake retrying workers before the executor joins on a fatal error.
            cleanup.callback(control.aborted.set)
            futures = {}
            while True:
                if control.state() == 'stop':
                    raise DownloadStopped('Stopped by user; resumable cache retained')
                additions = []
                while not exhausted and len(running_hours) < parallel_hours and control.state() != 'pause':
                    if (args.max_hours and submitted_hours >= args.max_hours) or (args.time_budget and time.monotonic() - start_clock >= args.time_budget):
                        exhausted = True
                        break
                    stamp = next(pending, None)
                    if stamp is None:
                        exhausted = True
                        break
                    hour = f'{stamp:%Y%m%d_%H}'
                    cache = folder / '.incomplete' / hour
                    state = dict(stamp=stamp, path=folder / (hour + '.nc'), cache=cache,
                                 arrays={}, attrs={}, tasks=[], remaining=0)
                    running_hours[hour] = state
                    submitted_hours += 1
                    records['hours'][hour] = dict(state='downloading', file=state['path'].name,
                                                  cache_dir=str(cache.relative_to(folder)))
                    event('hour_started', hour=hour, cached_parts=len(list(cache.rglob('*.bin'))) if cache.exists() else 0)
                    for name, store, var, levels in sources:
                        tidx = time_base[store.url] + int((stamp - times[0]).total_seconds() / 3600)
                        for key, pos, indices, shape in make_tasks(store, var, tidx, levels, iy, ix):
                            if name not in state['arrays']:
                                state['arrays'][name] = np.empty(shape, dtype='f4')
                                state['attrs'][name] = store.attrs(var)
                            state['tasks'].append((name, store, var, key, pos, indices))
                    state['remaining'] = len(state['tasks'])
                    state['total_tasks'] = state['remaining']
                    additions.append((hour, state))
                # Interleave initial jobs so all active hours make progress.
                if additions:
                    for task_index in range(max(len(state['tasks']) for _, state in additions)):
                        for hour, state in additions:
                            if task_index < len(state['tasks']):
                                name, store, var, key, pos, indices = state['tasks'][task_index]
                                future = pool.submit(read_cached_task, http, state['cache'], store, var, key, indices)
                                futures[future] = (hour, name, pos)
                    for _, state in additions:
                        del state['tasks']
                    atomic_json(records_path, records)
                    save_status()
                if not futures:
                    if control.state() == 'pause' and not exhausted:
                        save_status()
                        time.sleep(.2)
                        continue
                    break
                finished, _ = wait(futures, timeout=2, return_when=FIRST_COMPLETED)
                for future in finished:
                    hour, name, pos = futures.pop(future)
                    state = running_hours[hour]
                    values = future.result()
                    if not np.all(np.isfinite(values)):
                        raise ValueError(f'Missing/nonfinite source values in {name} at {hour}')
                    state['arrays'][name].ravel()[pos] = values
                    state['remaining'] -= 1
                    if state['remaining']:
                        continue
                    arrays, attrs = state['arrays'], state['attrs']
                    if 'sp' in arrays:
                        if np.any(arrays['sp'] <= 0):
                            raise ValueError('Nonpositive surface pressure')
                        arrays['lnsp'] = np.log(arrays['sp'])
                        attrs['lnsp'] = {'units': '1', 'long_name': 'Logarithm of surface pressure (Pa)', 'derived_from': 'surface_pressure'}
                    path, stamp, cache = state['path'], state['stamp'], state['cache']
                    write_hour(path, stamp, arrays, attrs, lat[iy], lon[ix], lev, cfg, signature)
                    records['hours'][hour] = check_file(path, stamp)
                    complete.append(path.name)
                    complete.sort()
                    downloaded += 1
                    atomic_json(records_path, records)
                    event('hour_completed', hour=hour, **records['hours'][hour])
                    if cache.exists():
                        cleanup_target = cache.resolve()
                        cleanup_root = (folder / '.incomplete').resolve()
                        if cleanup_target.parent != cleanup_root or cleanup_target.name != hour:
                            raise ValueError('Cache cleanup target outside current-hour cache')
                        shutil.rmtree(cleanup_target)
                    current_hour = hour
                    del running_hours[hour]
                    print(f'{stamp:%Y-%m-%d %H}:00 UTC saved; {len(complete)}/{len(times)}; network {http.bytes / 1e6:.1f} MB; elapsed {time.monotonic()-start_clock:.1f}s', flush=True)
                save_status()
    except DownloadStopped:
        event('user_stopped', active_hours=sorted(running_hours))
    except BaseException as exc:
        error = f'{type(exc).__name__}: {exc}'
        event('run_interrupted', hour=current_hour, error=error)
        raise
    finally:
        active = False
        http.progress_callback = None
        atomic_json(records_path, records)
        status = save_status()
        event('run_ended', completed_hours=len(complete), error=error)
        print(json.dumps({k:v for k,v in status.items() if k != 'completed_files'}, ensure_ascii=False), flush=True)
        try:
            if error is None and len(complete) == len(times):
                from merge_downloads import merge_downloads
                merge_downloads(folder)
        finally:
            request_lock.close()


if __name__ == '__main__':
    main()
