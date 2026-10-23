"""Retiring obsolete reports must not conceal missing or changed raw evidence."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from contextual_memory.evidence_integrity import audit_hashes


class RetirementTests(unittest.TestCase):
    def test_only_named_report_with_matching_original_hash_can_be_retired(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'notes').mkdir()
            name='results/full/ehr/PRELIMINARY_RESULTS.md'
            record={'deleted_files':{name:{'kind':'obsolete_report','sha256':'expected'}}}
            (root/'notes/EHR_EVALUATION_RETIREMENT.json').write_text(json.dumps(record))
            self.assertEqual(audit_hashes(root,{name:'expected'})[name],'retired_report')
            with self.assertRaises(ValueError):audit_hashes(root,{name:'different'})
            with self.assertRaises(ValueError):audit_hashes(root,{'results/full/ehr/episodes.json':'expected'})

    def test_existing_evidence_must_match_even_if_a_deletion_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'PRELIMINARY_RESULTS.md';path.write_text('changed')
            (root/'notes').mkdir()
            record={'deleted_files':{'PRELIMINARY_RESULTS.md':{'kind':'obsolete_report','sha256':'old'}}}
            (root/'notes/EHR_EVALUATION_RETIREMENT.json').write_text(json.dumps(record))
            with self.assertRaises(ValueError):audit_hashes(root,{'PRELIMINARY_RESULTS.md':'old'})
            current=hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(audit_hashes(root,{'PRELIMINARY_RESULTS.md':current})['PRELIMINARY_RESULTS.md'],'unchanged')

    def test_maintenance_never_exempts_native_checker_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'EhrAgent/ehragent/consistency.py';path.parent.mkdir(parents=True);path.write_text('changed')
            (root/'notes').mkdir()
            name=str(path.relative_to(root));after=hashlib.sha256(path.read_bytes()).hexdigest()
            record={'maintenance_files':{name:{'before_sha256':'old','after_sha256':after}}}
            (root/'notes/EHR_EVALUATION_RETIREMENT.json').write_text(json.dumps(record))
            with self.assertRaises(ValueError):audit_hashes(root,{name:'old'})


if __name__=='__main__':unittest.main()
