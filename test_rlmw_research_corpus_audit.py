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

def brute_force_first_dependency(rows, cap):
    mat=corpus.rows_to_mat(rows); n=len(mat[0]); cols=[]
    for j in range(n):
        cols.append(tuple(row[j] for row in mat))
    for w in range(1, cap+1):
        for supp in __import__('itertools').combinations(range(n), w):
            syn=[0]*len(mat)
            for j in supp:
                syn=[a^b for a,b in zip(syn, cols[j])]
            if not any(syn): return list(supp)
    return None

def deterministic_rows(r,n,seed):
    x=seed & 0xffffffff; rows=[]
    for _ in range(r):
        bits=[]
        for _ in range(n):
            x ^= (x << 13) & 0xffffffff; x ^= x >> 17; x ^= (x << 5) & 0xffffffff
            bits.append(str(x & 1))
        rows.append(''.join(bits))
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


    def test_randomized_tiny_differential_oracle(self):
        for n in range(3, 11):
            for r in range(1, min(n,5)+1):
                for seed in range(1, 8):
                    rows=deterministic_rows(r,n,1000*n+31*r+seed)
                    for cap in range(0, min(6,n)+1):
                        expected=brute_force_first_dependency(rows, cap)
                        rec=audit.ordered_split_search(rows, cap)
                        if expected is None:
                            self.assertEqual(rec['status'], 'CERTIFIED_LOWER_BOUND')
                            self.assertEqual(rec['certified_bound'], cap+1)
                        else:
                            self.assertEqual(rec['status'], 'CERTIFIED_EXACT_DISTANCE')
                            self.assertEqual(rec['witness_weight'], len(expected))
                            audit.verify_support(rows, rec['witness_support'])

    def test_resource_limits_and_counters(self):
        rows=h_from_support(8, [0,1,2])
        zero=audit.ordered_split_search(rows, 6, interrupt_after_subsets=0)
        self.assertEqual(zero['per_weight'][0]['examined_right_subsets'], 0)
        self.assertEqual(zero['per_weight'][0]['outcome'], 'INTERRUPTED')
        boundary=audit.ordered_split_search(rows, 6, interrupt_after_subsets=9) # weight-1 right+left complete for n=8
        self.assertEqual(boundary['last_completed_weight'], 1)
        self.assertEqual(boundary['per_weight'][0]['outcome'], 'EXHAUSTED_NO_WITNESS')
        during_left=audit.ordered_split_search(rows, 2, interrupt_after_subsets=9+8+1)
        self.assertEqual(during_left['per_weight'][-1]['outcome'], 'INTERRUPTED')
        self.assertEqual(during_left['per_weight'][-1]['examined_left_subsets'], 1)

    def test_canonical_jsonl_rejections_and_summary_refuses_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            path=audit.write_audit(6,d)
            recs=audit.read_canonical_jsonl(path)
            self.assertEqual(len(recs), 12)
            data=open(path,'rb').read()
            for suffix in [b'\n', b'  \n']:
                bad=os.path.join(d,'bad.jsonl')
                open(bad,'wb').write(data.splitlines()[0]+suffix+data.splitlines()[1:][0]+b'\n')
                with self.assertRaises(ValueError): audit.read_canonical_jsonl(bad)
            bad=os.path.join(d,'dupe.jsonl')
            line=data.splitlines()[0].decode()
            open(bad,'w',encoding='utf-8').write(line[:-1]+',"case_id":"hnrv1-c0012"}\n')
            with self.assertRaises(ValueError): audit.read_canonical_jsonl(bad)
            tampered=copy.deepcopy(recs[0]); tampered['status']='CERTIFIED_LOWER_BOUND'; tampered['record_sha256']=audit.sha({k:v for k,v in tampered.items() if k!='record_sha256'})
            bad=os.path.join(d,'tamper.jsonl')
            open(bad,'w',encoding='utf-8').write(audit.canonical_json_bytes(tampered).decode()+'\n')
            with self.assertRaises(ValueError): audit.main(['summary', bad])

    def test_preflight_rank_redundant_rows(self):
        rows=['1100','1100','0000']
        pf=audit.preflight(4,3,2,audit.gf2_rank_rows(rows))
        self.assertEqual(pf['rank_H'], 1)
        self.assertLessEqual(pf['weights'][0]['peak_map_bound'], 2)

    def test_schema_rejects_missing_witness_and_inconsistent_bounds(self):
        m=audit.load_manifest(); c=audit.case_map(m)['hnrv1-c0020']; rec=audit.audit_case(c,6)
        bad=copy.deepcopy(rec); bad.pop('witness_support'); bad['record_sha256']=audit.sha({k:v for k,v in bad.items() if k!='record_sha256'})
        with self.assertRaises(ValueError): audit.validate_record(bad,m)
        bad=copy.deepcopy(rec); bad['certified_bound']=4; bad['record_sha256']=audit.sha({k:v for k,v in bad.items() if k!='record_sha256'})
        with self.assertRaises(ValueError): audit.validate_record(bad,m)

    def test_xorshift_inapplicable_is_explicit_true(self):
        self.assertTrue(audit.dense_xorshift_relation_holds(10,8,1))

    def test_cli_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path=audit.write_audit(6,d)
            audit.main(['validate', path])
            audit.main(['validate', path, '--replay-exclusion'])

if __name__ == '__main__':
    unittest.main()
