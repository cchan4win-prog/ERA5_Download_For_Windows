"""V2 sequential site/period queue, using one process for all download workers."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time

import download_google_era5 as engine

ROOT = Path(__file__).resolve().parent
SETTINGS = {'site', 'start', 'end', 'time_zone', 'hours_time_zone', 'hours',
            'spatial_mode', 'latitude', 'longitude', 'north', 'south', 'west', 'east',
            'levels', 'variables', 'include_height_inputs', 'workers', 'parallel_hours',
            'timeout_seconds', 'retries', 'output_dir', 'generate_real_height'}
DEFAULTS = dict(time_zone='beijing', hours=list(range(24)), spatial_mode='point',
                levels='all', variables=['u','v','t'], include_height_inputs=True,
                workers=24, parallel_hours=3, timeout_seconds=45, retries=4, output_dir='data', generate_real_height=None)


def zone_name(value):
    if str(value).lower() == 'utc':
        return 'UTC'
    if str(value).lower() in ('beijing', 'asia/shanghai', '北京时间'):
        return 'beijing'
    raise ValueError('time_zone must be beijing or UTC')


def expand_jobs(config):
    """Validate the entire queue before opening a network connection."""
    batch = 'tasks' in config
    tasks = config.get('tasks', [config])
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('tasks must be a nonempty list')
    shared = deepcopy(DEFAULTS)
    shared.update({k: deepcopy(v) for k,v in config.items() if k in SETTINGS})
    result = []
    for task_index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError('Each task must be an object')
        if task.get('enabled', True) is False:
            continue
        base = deepcopy(shared)
        base.update({k: deepcopy(v) for k,v in task.items() if k in SETTINGS})
        periods = task.get('periods', task.get('time_ranges', [task]))
        if not isinstance(periods, list) or not periods:
            raise ValueError('Each site needs at least one period')
        for period in periods:
            if not isinstance(period, dict):
                raise ValueError('Each period must be an object with start/end')
            job = deepcopy(base)
            job.update({k: deepcopy(v) for k,v in period.items() if k in SETTINGS})
            if not isinstance(job.get('site'), str) or not job['site'].strip():
                raise ValueError('Each task needs a site name')
            for key in ('start', 'end'):
                if key not in job:
                    raise ValueError('Each period needs start and end')
                value = job[key]
                if isinstance(value, str) and len(value) == 10:
                    job[key] = value + ('T00:00:00' if key == 'start' else 'T23:00:00')
            # V2 presents two consistent choices. Legacy single-task Shanghai
            # configs retain their historical UTC hour-filter behavior.
            if batch:
                job['time_zone'] = zone_name(job['time_zone'])
                if 'hours_time_zone' in job:
                    job['hours_time_zone'] = zone_name(job['hours_time_zone'])
            engine.selected_times(job)
            if type(job['parallel_hours']) is not int or not 1 <= job['parallel_hours'] <= 16:
                raise ValueError('parallel_hours must be an integer from 1 to 16')
            if type(job['workers']) is not int or not 1 <= job['workers'] <= 64:
                raise ValueError('workers must be an integer from 1 to 64')
            if job['timeout_seconds'] <= 0 or type(job['retries']) is not int or job['retries'] < 0:
                raise ValueError('Invalid timeout/retries')
            if job['spatial_mode'] == 'point':
                lat, lon = job.get('latitude'), job.get('longitude')
                if isinstance(lat, bool) or isinstance(lon, bool) or not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)) or not -90 <= lat <= 90 or not -180 <= lon <= 360:
                    raise ValueError('Fill in valid latitude/longitude for site ' + job['site'])
            elif job['spatial_mode'] == 'bbox':
                if not all(isinstance(job.get(k), (int,float)) for k in ('north','south','west','east')) or not -90 <= job['south'] <= job['north'] <= 90:
                    raise ValueError('Invalid bbox for site ' + job['site'])
            else:
                raise ValueError('spatial_mode must be point or bbox')
            if not isinstance(job['variables'], list) or not job['variables'] or any(v not in engine.ALIASES for v in job['variables']):
                raise ValueError('Invalid variables for ' + job['site'])
            levels = job['levels']
            if levels != 'all' and (not isinstance(levels,list) or not levels or any(type(v) is not int or not 1 <= v <= 137 for v in levels) or len(set(levels)) != len(levels)):
                raise ValueError('levels must be all or a unique integer list')
            if job['include_height_inputs'] and levels != 'all' and levels != list(range(1,138)):
                raise ValueError('Height inputs require all 137 model levels')
            if job['generate_real_height'] is None:
                job['generate_real_height'] = bool(job['include_height_inputs'])
            if job['generate_real_height'] and (not job['include_height_inputs'] or (levels!='all' and levels!=list(range(1,138)))):
                raise ValueError('Real-height output requires height inputs and all 137 levels')
            result.append(job)
    if not result:
        raise ValueError('No enabled download tasks')
    return result


def read_json(path, fallback=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return fallback


def run_queue(config, session, status_path, control_path, runner=None):
    jobs = expand_jobs(config)
    session = Path(session).resolve()
    session.mkdir(parents=True, exist_ok=True)
    status_path, control_path = Path(status_path), Path(control_path)
    runner = runner or engine.main
    control = engine.DownloadControl(control_path)
    identities = []
    for job in jobs:
        identity = {k:v for k,v in job.items() if k not in {'workers','parallel_hours','timeout_seconds','retries','time_zone','hours_time_zone','start','end','hours','generate_real_height'}}
        identity['selected_utc_hours'] = [t.isoformat() for t in engine.selected_times(job)]
        identities.append(identity)
    fingerprint = hashlib.sha256(json.dumps(identities,sort_keys=True).encode()).hexdigest()[:16]
    records_dir = ROOT / '.queue_records'
    records_dir.mkdir(exist_ok=True)
    record_path = records_dir / (fingerprint + '.json')
    previous = read_json(record_path, {})
    rows = []
    for index, job in enumerate(jobs):
        times = engine.selected_times(job)
        folder, signature = engine.request_folder(job)
        done = sum((folder / f'{stamp:%Y%m%d_%H}.nc').exists() for stamp in times)
        rows.append(dict(index=index+1, site=job['site'], start=job['start'], end=job['end'],
                         time_zone=job['time_zone'], requested_hours=len(times), completed_hours=done,
                         folder=str(folder), signature=signature, state='pending', error=None))
    engine.atomic_json(session / 'queue_plan.json', dict(jobs=jobs, tasks=rows))
    context = dict(index=0, phase='preparing', state='running', error=None, worker_status=None)
    totals = dict(active=float(previous.get('elapsed_active_seconds', 0)), paused=float(previous.get('paused_seconds',0)))
    last_tick = time.monotonic()
    last_mode = control.state()
    stop_monitor = threading.Event()
    publish_lock = threading.Lock()
    counters = dict(network=0, cache=0)

    def publish():
        nonlocal last_tick, last_mode
        with publish_lock:
            current = dict(context)  # Never mix two tasks during a transition.
            now, mode = time.monotonic(), control.state()
            totals['paused' if last_mode == 'pause' else 'active'] += now - last_tick
            last_tick, last_mode = now, mode
            index = current['index']
            row = rows[min(index, len(rows)-1)]
            raw = read_json(current['worker_status'], {}) if current['worker_status'] else {}
            raw = raw or {}
            if raw:
                row['completed_hours'] = int(raw.get('completed_hours', row['completed_hours']))
                row['folder'] = raw.get('data_folder', row['folder'])
            phase = current['phase']
            if phase == 'downloading' and raw.get('complete'):
                phase = 'merging'
            rate = raw.get('seconds_per_hour')
            remaining_here = raw.get('eta_seconds')
            remaining_future = sum(max(0,r['requested_hours']-r['completed_hours']) for r in rows[index+1:])
            eta = remaining_here + remaining_future * rate if remaining_here is not None and rate is not None and phase == 'downloading' else None
            overall_done = sum(r['completed_hours'] for r in rows)
            overall_total = sum(r['requested_hours'] for r in rows)
            state = current['state']
            if mode == 'pause' and state == 'running' and phase != 'merging':
                state = 'paused'
            status = dict(raw)
            status.update(run_state=state, phase=phase, queue_version=2,
                          queue_task_index=min(index+1,len(rows)), queue_task_count=len(rows),
                          queue_completed_tasks=sum(r['state']=='complete' for r in rows),
                          queue_completed_hours=overall_done, queue_total_hours=overall_total,
                          task_site=row['site'], task_start=row['start'], task_end=row['end'],
                          task_time_zone=row['time_zone'], parallel_hours=jobs[index]['parallel_hours'],
                          requested_hours=row['requested_hours'], completed_hours=row['completed_hours'],
                          data_folder=row['folder'], queue_tasks=[dict(r) for r in rows],
                          elapsed_active_seconds=totals['active'], paused_seconds=totals['paused'],
                          current_task_eta_seconds=raw.get('eta_seconds'), eta_seconds=eta,
                          height_percent=current.get('height_percent',0),
                          eta_note='Estimated from current task rate; merge time not included',
                          network_bytes_this_run=counters['network']+raw.get('network_bytes_this_run',0),
                          reused_cache_bytes_this_run=counters['cache']+raw.get('reused_cache_bytes_this_run',0),
                          process_id=__import__('os').getpid(), error=current['error'],
                          updated_utc=datetime.now(timezone.utc).isoformat())
            if state == 'complete':
                status['eta_seconds'] = 0
            engine.atomic_json(status_path, status)
            record = dict(queue_version=2, tasks=[dict(r) for r in rows], state=state,
                          elapsed_active_seconds=totals['active'], paused_seconds=totals['paused'],
                          updated_utc=status['updated_utc'], error=current['error'])
            engine.atomic_json(record_path, record)
            return status

    def monitor():
        while not stop_monitor.wait(.7):
            try:
                publish()
            except OSError:
                pass  # GUI readers can briefly hold a file open on Windows.

    thread = threading.Thread(target=monitor, daemon=True)
    publish()
    thread.start()
    try:
        for index, job in enumerate(jobs):
            context.update(index=index,phase='preparing',worker_status=None)
            publish()
            control.state(force=True)
            control.wait()
            config_path = session / f'task_{index+1:03d}.json'
            engine.atomic_json(config_path, job)
            worker_status = session / f'task_{index+1:03d}_status.json'
            rows[index]['state'] = 'running'
            context.update(phase='downloading',worker_status=worker_status)
            print(f"Task {index+1}/{len(jobs)}: {job['site']} {job['start']} to {job['end']}",flush=True)
            runner(['--config',str(config_path),'--status-file',str(worker_status),'--control-file',str(control_path)])
            publish()
            if control.state(force=True) == 'stop':
                raise engine.DownloadStopped('Queue cancelled by user')
            raw = read_json(worker_status,{})
            if not raw.get('complete'):
                raise RuntimeError('Current task exited before all expected hours completed')
            merge = read_json(Path(raw['data_folder']) / 'merge_status.json',{})
            if merge.get('state') != 'complete':
                raise RuntimeError('Current task has no verified merged NetCDF')
            if job['generate_real_height']:
                from compute_real_heights import convert_real_heights
                context.update(phase='height',height_percent=0)
                publish()
                def height_progress(value): context['height_percent']=round(100*value,1)
                height_file=convert_real_heights(merge['output'],control=control,progress=height_progress)
                rows[index]['height_file']=str(height_file)
            rows[index].update(state='complete',completed_hours=rows[index]['requested_hours'],merged_file=merge['output'])
            counters['network'] += raw.get('network_bytes_this_run',0)
            counters['cache'] += raw.get('reused_cache_bytes_this_run',0)
            context['worker_status'] = None
            publish()
        context.update(state='complete',phase='complete')
    except engine.DownloadStopped:
        rows[context['index']]['state'] = 'cancelled'
        context.update(state='cancelled',phase='cancelled')
    except BaseException as exc:
        rows[context['index']].update(state='failed',error=str(exc))
        context.update(state='failed',phase='failed',error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        stop_monitor.set()
        thread.join(timeout=3)
        status = publish()
        print(json.dumps({k:status[k] for k in ('run_state','queue_completed_tasks','queue_task_count','elapsed_active_seconds','error')},ensure_ascii=False),flush=True)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'config.json')
    parser.add_argument('--status-file',type=Path)
    parser.add_argument('--control-file',type=Path)
    parser.add_argument('--plan',action='store_true')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    jobs = expand_jobs(config)
    if args.plan:
        print(json.dumps(dict(tasks=[dict(site=j['site'],start=j['start'],end=j['end'],hours=len(engine.selected_times(j)),parallel_hours=j['parallel_hours'],folder=str(engine.request_folder(j)[0])) for j in jobs]),ensure_ascii=False,indent=2))
        return
    session = args.status_file.parent if args.status_file else ROOT/'.gui_sessions'/('queue_'+datetime.now().strftime('%Y%m%d_%H%M%S%f'))
    session.mkdir(parents=True,exist_ok=True)
    control = args.control_file or session/'control.json'
    if not control.exists(): engine.atomic_json(control,dict(command='run'))
    run_queue(config,session,args.status_file or session/'status.json',control)


if __name__ == '__main__':
    main()
