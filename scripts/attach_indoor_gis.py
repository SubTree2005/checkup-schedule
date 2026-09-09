"""Create a reviewable workspace copy using evidenced GIS, preserving business data."""
import argparse
import json
from pathlib import Path
from checkup_scheduler.gis import attach_gis_to_workspace


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('workspace',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--generated',type=Path,default=Path(__file__).resolve().parents[1]/'gis/generated')
    args=p.parse_args()
    if args.output.resolve()==args.workspace.resolve():
        p.error('--output must be a new reviewable file, not the input workspace')
    result=attach_gis_to_workspace(json.loads(args.workspace.read_text(encoding='utf-8')),args.generated)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__': main()
