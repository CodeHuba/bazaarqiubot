"""Read-only event image preview. Never imports QQ or sends messages.

python tools/preview_secondary_hero_report.py --db runs.db --hero 杜利
"""
import argparse
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType('_secondary_preview')
package.__path__ = [str(ROOT/'plugins/bazaar_plugin')]
sys.modules[package.__name__] = package
stats = importlib.import_module(package.__name__+'.secondary_hero_stats')
cards = importlib.import_module(package.__name__+'.secondary_hero_image')
names = importlib.import_module(package.__name__+'.runs_query')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--hero',default='')
    parser.add_argument('--sort',choices=('pick','win'),default='pick')
    parser.add_argument('--source-note',default='')
    parser.add_argument('--output',type=Path,default=ROOT/'data/charts/secondary-preview')
    args = parser.parse_args()
    try:
        hero,_ = stats.parse_args(args.hero,names.HERO_EN_TO_ZH,names.HERO_ZH_TO_EN)
    except ValueError as exc:
        parser.error(str(exc))
    event = stats.load_event(args.config)
    result = stats.query_stats(names.HERO_EN_TO_ZH,args.db,event=event)
    result['source_note'] = args.source_note
    doc = stats.build_document(result,hero=hero,order=args.sort,preview=True)
    path = Path(cards.render_report(doc,args.output))
    path.with_suffix('.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
    path.with_suffix('.txt').write_text(stats.format_report(doc),encoding='utf-8')
    print(path)


if __name__=='__main__':
    main()
