import copy, json, os, tempfile, unittest

import rlmw_research_corpus as corpus
import rlmw_research_corpus_audit as audit


def h_from_support(n, support):
    # Kernel is the span of exactly the requested all-one support for small controls.
    supp=set(support); piv=[j for j in range(n) if j not in supp]
    rows=[]
    anchor=min(supp)
    for j in piv:
        row=['0']*n; row[j]='1'; rows.append(''.join(row))
    for j in sorted(supp-{anchor}):
        row=['0']*n; row[anchor]='1'; row[j]='1'; rows.append(''.join(row))
    return rows

class AuditTests(unittest.TestCase):
    def test_exact_distances_1_through_6_and_lower_bound_7(self):
        for d in range(1,7):
            rows=h_from_support(d+3, list(range(d)))
            rec=audit.ordered_split_search(rows,6)
            self.assertEqual(rec['status'], 'CERTIFIED_EXACT_DISTANCE')
            self.assertEqual(rec['witness_weight'], d)
        rows=h_from_support(10, list(range(7)))
        rec=audit.ordered_split_search(rows,6)
        self.assertEqual(rec['status'], 'CERTIFIED_LOWER_BOUND')
        self.assertEqual(rec['certified_bound'], 7)

    def test_hamming_controls(self):
        self.assertEqual(audit.ordered_split_search(corpus.hamming_H(4),6)['witness_weight'], 3)
        self.assertEqual(audit.ordered_split_search(corpus.extended_hamming_H(4),6)['witness_weight'], 4)

    def test_odd_even_overlap_representative_and_permutation(self):
        rows=h_from_support(12, [2,4,5,9,10])
        rec=audit.ordered_split_search(rows,6)
        self.assertEqual(rec['witness_support'], [2,4,5,9,10])
        self.assertTrue(audit.representative_better((6,7,8), (1,10,11)))
        perm=[3,1,2,0,4,5,6,7,8,9,10,11]
        prows=[''.join(row[perm[i]] for i in range(len(perm))) for row in rows]
        audit.verify_support(prows, sorted(perm.index(j) for j in [2,4,5,9,10]))
        # redundant row / row operation preserves witnesses.
        r2=rows + [rows[0]]
        audit.verify_support(r2, [2,4,5,9,10])

    def test_resource_boundary_no_interrupted_exclusion(self):
        rec=audit.ordered_split_search(h_from_support(12, list(range(6))), 6, interrupt_after_subsets=1)
        self.assertEqual(rec['status'], 'RESOURCE_LIMIT')
        self.assertEqual(rec['last_completed_weight'], 0)

    def test_record_tampering_and_replay(self):
        m=audit.load_manifest(); c=audit.case_map(m)['hnrv1-c0020']; rec=audit.audit_case(c,6)
        audit.validate_record(rec,m,True)
        bad=copy.deepcopy(rec); bad['raw_H_sha256']='0'*64; bad['record_sha256']=audit.sha({k:v for k,v in bad.items() if k!='record_sha256'})
        with self.assertRaises(ValueError): audit.validate_record(bad,m,False)
        bad=copy.deepcopy(rec); bad['witness_support']=[1,2,3]; bad['record_sha256']=audit.sha({k:v for k,v in bad.items() if k!='record_sha256'})
        with self.assertRaises(ValueError): audit.validate_record(bad,m,False)
        with self.assertRaises(ValueError): audit.ordered_split_search(c['H_rows'], True)

    def test_xorshift_structural_relation(self):
        for n,r,seed in [(96,48,7707),(128,64,8808),(144,72,9909),(160,80,3030)]:
            self.assertTrue(audit.dense_xorshift_relation_holds(n,r,seed))

    def test_all_twelve_reproduced_findings(self):
        m=audit.load_manifest(); cm=audit.case_map(m)
        for cid, (_, w, supp) in audit.EXPECTED_FINDINGS.items():
            rec=audit.audit_case(cm[cid],6)
            self.assertEqual(rec['status'], 'CERTIFIED_EXACT_DISTANCE')
            self.assertEqual(rec['witness_weight'], w)
            self.assertEqual(rec['witness_support'], supp)
        for cid in audit.LOWER_BOUND_CASES:
            rec=audit.audit_case(cm[cid],6)
            self.assertEqual(rec['status'], 'CERTIFIED_LOWER_BOUND')
            self.assertEqual(rec['certified_bound'], 7)

    def test_cli_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path=audit.write_audit(6,d)
            audit.main(['validate', path])
            audit.main(['validate', path, '--replay-exclusion'])

if __name__ == '__main__':
    unittest.main()
