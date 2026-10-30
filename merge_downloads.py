#!/usr/bin/env python3
"""Merge a complete request into one NetCDF, preserving hourly originals."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import argparse

from download_google_era5 import Dataset, np, open_nc, atomic_json, date2num, selected_times


def expected_times(cfg):
    return selected_times(cfg)


def merge_downloads(folder):
    folder = Path(folder).resolve()
    request = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
    cfg, signature = request['config'], request['signature']
    times = expected_times(cfg)
    paths = [folder / f'{stamp:%Y%m%d_%H}.nc' for stamp in times]
    missing = [path.name for path in paths if not path.exists()]
    if missing:
        raise ValueError(f'Refusing incomplete merge: {len(missing)} hours missing; first: {missing[:5]}')
    report_path = folder / 'merge_status.json'
    output = folder / 'merged' / f"{cfg['site']}_{times[0]:%Y%m%d_%H}_{times[-1]:%Y%m%d_%H}_merged.nc"
    output.parent.mkdir(exist_ok=True)
    report = dict(state='merging', expected_hours=len(times), output=str(output),
                  started_utc=datetime.now(timezone.utc).isoformat())
    atomic_json(report_path, report)
    ds = None
    try:
        records_path = folder / 'download_records.json'
        records = json.loads(records_path.read_text(encoding='utf-8'))['hours'] if records_path.exists() else {}
        with open_nc(paths[0]) as first:
            names = set(first.variables)
            if not {'time', 'level', 'latitude', 'longitude'}.issubset(names):
                raise ValueError('Required coordinates missing')
            ds = Dataset('merged.nc', 'w', memory=1024)
            for name, dim in first.dimensions.items():
                ds.createDimension(name, len(times) if name == 'time' else len(dim))
            ds.setncatts({name: first.getncattr(name) for name in first.ncattrs()})
            ds.merged_hour_count = len(times)
            ds.merge_note = 'Chronological concatenation; hourly originals retained; no interpolation'
            for name, var in first.variables.items():
                opts = dict(zlib=True, complevel=4) if 'time' in var.dimensions else {}
                if '_FillValue' in var.ncattrs():
                    opts['fill_value'] = var.getncattr('_FillValue')
                dest = ds.createVariable(name, var.dtype, var.dimensions, **opts)
                dest.setncatts({a: var.getncattr(a) for a in var.ncattrs() if a != '_FillValue'})
                if 'time' not in var.dimensions:
                    dest[:] = var[:]
        for index, (path, stamp) in enumerate(zip(paths, times)):
            expected_sha = records.get(path.stem, {}).get('sha256')
            if expected_sha and hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha:
                raise ValueError(f'Checksum mismatch: {path.name}')
            with open_nc(path) as src:
                if src.config_signature != signature or set(src.variables) != names:
                    raise ValueError(f'Request or variable mismatch: {path.name}')
                if len(src.dimensions['time']) != 1:
                    raise ValueError(f'Expected exactly one hour: {path.name}')
                for name, var in src.variables.items():
                    dest = ds[name]
                    if var.dimensions != dest.dimensions or var.dtype != dest.dtype:
                        raise ValueError(f'Variable layout mismatch: {path.name} {name}')
                    for attr in ('units', 'calendar'):
                        if getattr(var, attr, None) != getattr(dest, attr, None):
                            raise ValueError(f'Variable units/calendar mismatch: {path.name} {name}')
                    data = var[:]
                    if np.ma.getmaskarray(data).any() or not np.isfinite(data).all():
                        raise ValueError(f'Incomplete variable: {path.name} {name}')
                    if 'time' not in var.dimensions:
                        np.testing.assert_array_equal(data, dest[:])
                    else:
                        if var.dimensions[0] != 'time' or data.shape[1:] != dest.shape[1:]:
                            raise ValueError(f'Unsupported time layout: {name}')
                        dest[index:index+1] = data
                expected = date2num(stamp, ds['time'].units, ds['time'].calendar)
                if src['time'][0] != expected:
                    raise ValueError(f'Time mismatch: {path.name}')
        image = ds.close()
        ds = None
        temp = output.with_suffix('.nc.partial')
        temp.write_bytes(image)
        del image
        # Reopen the final image and compare every value against every input.
        with open_nc(temp) as merged:
            if len(merged.dimensions['time']) != len(times) or not (np.diff(merged['time'][:]) > 0).all():
                raise ValueError('Merged time axis is invalid')
            for index, path in enumerate(paths):
                with open_nc(path) as src:
                    for name, var in src.variables.items():
                        data = merged[name][index:index+1] if 'time' in var.dimensions else merged[name][:]
                        np.testing.assert_array_equal(data, var[:])
        temp.replace(output)
        report.update(state='complete', verified_hours=len(times), bytes=output.stat().st_size,
                      sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                      completed_utc=datetime.now(timezone.utc).isoformat())
        atomic_json(report_path, report)
        print(f'Merged and verified {len(times)} hours: {output}', flush=True)
        return output
    except BaseException as exc:
        if ds is not None and ds.isopen():
            ds.close()
        report.update(state='failed', error=f'{type(exc).__name__}: {exc}')
        atomic_json(report_path, report)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path, help='Directory containing request.json and hourly NetCDF files')
    merge_downloads(parser.parse_args().folder)
