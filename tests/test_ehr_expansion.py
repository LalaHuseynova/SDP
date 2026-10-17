"""Offline coverage checks for the expanded saved-ID split."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/ehragent'))
import run_small

class Expansion(unittest.TestCase):
    def test_recorded_failures_allow_reporting_but_incomplete_runs_do_not(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            self.assertIsNone(run_small.recorded_failure_count(directory,2))
            (directory/'episodes.json').write_text(json.dumps([{'status':'ok'},{'status':'failure'}]))
            self.assertIsNone(run_small.recorded_failure_count(directory,2))
            (directory/'final_lessons.json').write_text('[]')
            self.assertEqual(run_small.recorded_failure_count(directory,2),1)
            self.assertIsNone(run_small.recorded_failure_count(directory,3))

    def test_full_coverage_disjoint_and_small_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);data=root/'EhrAgent/database/ehr_logs/eicu_ac.json';data.parent.mkdir(parents=True)
            rows=[dict(id=f'id-{i:03d}',template=f'question {i}?',answer=str(i)) for i in range(45)]
            data.write_text(json.dumps(rows+rows[:4]))
            small=root/'small';small.mkdir();full=root/'full';full.mkdir()
            memory=[dict(id='memory',question='question 0?',code='answer=0',knowledge='safe')]
            with patch.object(run_small,'ROOT',root),patch.object(run_small,'load_ehr_memory',return_value=memory):
                a=json.loads(run_small.select_split(small).read_text())
                b=json.loads(run_small.select_split(full,True).read_text())
            training={q['id'] for q in b['training']};evaluation={q['id'] for q in b['evaluation']}
            self.assertEqual(len(training),16);self.assertEqual(len(evaluation),29)
            self.assertFalse(training&evaluation);self.assertEqual(training|evaluation,{q['id'] for q in rows})
            self.assertEqual(a['evaluation'],b['evaluation'][:20])
            self.assertTrue({q['id'] for q in b['smoke']}<=training)
            self.assertEqual(b['source_rows'],49);self.assertEqual(b['unique_ids'],45)
            self.assertEqual(b['scope'],'all_held_out')

if __name__=='__main__':unittest.main()
