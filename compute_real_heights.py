"""ERA5 L137 hydrostatic pressure/geopotential and spherical geometric height.

Method: ECMWF CKB page 158636068; coefficients: ECMWF L137 definitions.
No vertical interpolation: height is a time-dependent 4-D auxiliary coordinate.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from download_google_era5 import ROOT, Dataset, np, open_nc, atomic_json

COEFFICIENTS = json.loads((ROOT / 'era5_l137_coefficients.json').read_text(encoding='utf-8'))
A = np.asarray(COEFFICIENTS['A_HALF'], dtype='f8')
B = np.asarray(COEFFICIENTS['B_HALF'], dtype='f8')
RD, G0, EARTH_RADIUS = 287.06, 9.80665, 6371000.
METHOD = 'https://confluence.ecmwf.int/spaces/CKB/pages/158636068'
ALGORITHM_SHA256 = hashlib.sha256(json.dumps(dict(A=COEFFICIENTS['A_HALF'],B=COEFFICIENTS['B_HALF'],
    RD=RD,G0=G0,radius=EARTH_RADIUS,humidity_factor=.609133,version=1),sort_keys=True).encode()).hexdigest()


def hydrostatic_heights(t, q, lnsp, surface_geopotential):
    t, q = np.asarray(t,dtype='f8'), np.asarray(q,dtype='f8')
    lnsp, surface_geopotential = np.asarray(lnsp,dtype='f8'), np.asarray(surface_geopotential,dtype='f8')
    if t.ndim != 4 or t.shape[1] != 137 or q.shape != t.shape:
        raise ValueError('T/Q must contain all 137 model levels in top-to-bottom order')
    if lnsp.shape != t.shape[:1]+t.shape[2:] or surface_geopotential.shape != lnsp.shape:
        raise ValueError('Surface field dimensions do not match model fields')
    if not all(np.isfinite(x).all() for x in (t,q,lnsp,surface_geopotential)) or (t<=0).any():
        raise ValueError('Nonfinite inputs or nonpositive absolute temperature')
    # Do not clip or silently change the downloaded humidity values.
    tv = t * (1. + .609133*q)
    if (tv<=0).any(): raise ValueError('Nonpositive virtual temperature')
    sp = np.exp(lnsp)
    if not np.isfinite(sp).all() or (sp<=0).any(): raise ValueError('Invalid surface pressure')
    half = A[None,:,None,None] + B[None,:,None,None]*sp[:,None]
    if not (np.diff(half,axis=1)>0).all(): raise ValueError('Half-level pressures are not monotonic')
    pressure = .5*(half[:,:-1]+half[:,1:])
    geopotential = np.empty_like(t)
    bottom_phi = surface_geopotential.copy()
    for j in range(136,-1,-1):
        upper, lower = half[:,j], half[:,j+1]
        if j == 0:
            dlog = np.log(lower/.1)
            alpha = np.log(2.)
        else:
            dlog = np.log(lower/upper)
            alpha = 1. - upper/(lower-upper)*dlog
        geopotential[:,j] = bottom_phi + RD*tv[:,j]*alpha
        bottom_phi += RD*tv[:,j]*dlog
    geopotential_height = geopotential/G0
    if (geopotential_height >= EARTH_RADIUS).any(): raise ValueError('Invalid spherical height denominator')
    geometric_height = EARTH_RADIUS*geopotential_height/(EARTH_RADIUS-geopotential_height)
    if not (np.diff(geometric_height,axis=1)<0).all(): raise ValueError('Model heights are not vertically monotonic')
    return dict(p_full=pressure,z_full=geopotential_height,h_full=geometric_height)


def convert_real_heights(source, output=None, control=None, progress=None, chunk_time=24):
    if control: control.wait()
    source = Path(source).resolve()
    output = Path(output).resolve() if output else source.with_name(source.stem.removesuffix('_merged')+'_real_height.nc')
    if source == output: raise ValueError('Height output must not overwrite the merged source')
    if chunk_time < 1: raise ValueError('chunk_time must be positive')
    report_path = output.with_suffix('.status.json')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = dict(state='computing',source=str(source),output=str(output),source_sha256=digest,
                  method=METHOD,algorithm_sha256=ALGORITHM_SHA256,started_utc=datetime.now(timezone.utc).isoformat())
    try:
        prior = json.loads(report_path.read_text(encoding='utf-8'))
        if prior.get('state')=='complete' and prior.get('source_sha256')==digest and prior.get('algorithm_sha256')==ALGORITHM_SHA256 and output.exists() and hashlib.sha256(output.read_bytes()).hexdigest()==prior.get('sha256'):
            if progress: progress(1.)
            return output
    except (OSError, ValueError):
        pass
    output.parent.mkdir(parents=True,exist_ok=True)
    atomic_json(report_path,report)
    dest = None
    try:
        with open_nc(source) as src:
            required=('time','level','latitude','longitude','t','q','lnsp','z')
            if any(v not in src.variables for v in required): raise ValueError('Real heights require T, Q, LNSP and SURFACE geopotential Z')
            if not np.array_equal(src['level'][:], np.arange(1,138)): raise ValueError('Real heights require model levels 1..137 in order')
            for name in ('t','q'):
                if src[name].dimensions != ('time','level','latitude','longitude'): raise ValueError('Wrong model field dimensions')
            for name in ('lnsp','z'):
                if src[name].dimensions != ('time','latitude','longitude'): raise ValueError('Z must be surface geopotential, not model-level height')
            def unit(name):return str(getattr(src[name],'units','')).lower().replace(' ','').replace('*','').replace('^','')
            if unit('t') not in ('k','kelvin'):raise ValueError('Temperature must be in kelvin (K)')
            if unit('q') not in ('kgkg-1','kg/kg','1'):raise ValueError('Specific humidity must be in kg/kg')
            if unit('z') not in ('m2s-2','m2/s2'):raise ValueError('Surface Z must be geopotential (m2/s2), not height in metres')
            nt=len(src.dimensions['time'])
            dest=Dataset('real_height.nc','w',memory=1024)
            for name,dim in src.dimensions.items(): dest.createDimension(name,len(dim))
            dest.setncatts({a:src.getncattr(a) for a in src.ncattrs()})
            dest.height_method=METHOD
            dest.height_datum='Geoid / mean sea level; spherical Earth approximation, R=6371000 m'
            dest.height_note='h_full is a 4-D geometric-height auxiliary coordinate; no interpolation to fixed altitude bins'
            dest.source_merged_sha256=digest
            dest.height_algorithm_sha256=ALGORITHM_SHA256
            for name,var in src.variables.items():
                opts=dict(zlib=True,complevel=4) if 'time' in var.dimensions else {}
                if '_FillValue' in var.ncattrs(): opts['fill_value']=var.getncattr('_FillValue')
                out=dest.createVariable(name,var.dtype,var.dimensions,**opts)
                out.setncatts({a:var.getncattr(a) for a in var.ncattrs() if a!='_FillValue'})
                if 'time' not in var.dimensions: out[:]=var[:]
                if var.dimensions==('time','level','latitude','longitude'): out.coordinates='h_full'
            for name,unit,title,std in [('p_full','Pa','Air pressure at model full levels','air_pressure'),
                    ('z_full','m','Geopotential height above geoid','geopotential_height'),
                    ('h_full','m','Geometric height above geoid (spherical approximation)','height')]:
                var=dest.createVariable(name,'f8',('time','level','latitude','longitude'),zlib=True,complevel=4)
                var.units=unit;var.long_name=title;var.standard_name=std
                if name!='p_full': var.positive='up'
            for start in range(0,nt,chunk_time):
                if control: control.wait()
                end=min(nt,start+chunk_time)
                for name,var in src.variables.items():
                    if 'time' in var.dimensions: dest[name][start:end]=var[start:end]
                inputs=[src[name][start:end] for name in ('t','q','lnsp','z')]
                if any(np.ma.getmaskarray(x).any() for x in inputs): raise ValueError('Missing hydrostatic input values')
                values=hydrostatic_heights(*inputs)
                for name,array in values.items(): dest[name][start:end]=array
                report.update(completed_hours=end,total_hours=nt)
                atomic_json(report_path,report)
                if progress: progress(.75*end/max(1,nt))
            image=dest.close();dest=None
            temp=output.with_suffix('.nc.partial');temp.write_bytes(image);del image
            with open_nc(temp) as verified:
                for start in range(0,nt,chunk_time):
                    if control: control.wait()
                    end=min(nt,start+chunk_time)
                    expected=hydrostatic_heights(*(src[n][start:end] for n in ('t','q','lnsp','z')))
                    for name,array in expected.items(): np.testing.assert_allclose(verified[name][start:end],array,rtol=1e-12,atol=1e-8)
                    for name,var in src.variables.items():
                        a=verified[name][start:end] if 'time' in var.dimensions else verified[name][:]
                        b=var[start:end] if 'time' in var.dimensions else var[:]
                        np.testing.assert_array_equal(a,b)
                    if progress: progress(.75+.25*end/max(1,nt))
            temp.replace(output)
            report.update(state='complete',verified_hours=nt,bytes=output.stat().st_size,
                          sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                          completed_utc=datetime.now(timezone.utc).isoformat())
            atomic_json(report_path,report)
            print(f'Real-height file computed and verified: {output}',flush=True)
            return output
    except BaseException as exc:
        if dest is not None and dest.isopen(): dest.close()
        report.update(state='failed',error=f'{type(exc).__name__}: {exc}')
        atomic_json(report_path,report)
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    convert_real_heights(args.source,args.output)
