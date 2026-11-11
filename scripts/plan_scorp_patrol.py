"""Offline connected-ground checks for a small spawn-area patrol."""
import json
from pathlib import Path
from agent_wow_session import PureNetworkBackend
from agent_wow_travel import prepare_return, plan_leg


def main():
    backend = PureNetworkBackend('offline_patrol')
    points = [[-778,-4985,19], [-731.343,-5038.89,15.7219],
              [-706.37,-5014.12,17.3949], [-688.159,-4987.74,19.7873],
              [-691.481,-4957.31,23.1926]]
    edges = []
    for start, end in zip(points, points[1:]+points[:1]):
        try:
            raw = backend._nav(start,end)
            backend.submitted_position = start
            leg = plan_leg(backend,end,radius=12)
            prepare_return(backend,[start],leg['points'])
            edges.append({'start':start,'end':end,'status':'route_and_first_leg_checked',
                          'samples':len(raw.get('surface_samples') or [])})
        except RuntimeError as exc:
            edges.append({'start':start,'end':end,'status':'rejected','error':str(exc)})
    report = {'scope':'offline geometry only; actual target level and hostility require live observation',
              'points':points,'edges':edges}
    path = Path('results/pure_network/scorp_patrol_plan_20261110_dj.json')
    with path.open('x',encoding='utf-8') as handle:
        json.dump(report,handle,indent=2)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
