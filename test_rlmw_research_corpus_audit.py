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


def resign(rec):
    rec['record_sha256']=audit.sha({k:v for k,v in rec.items() if k!='record_sha256'})
    return rec

def invertible_row_ops(rows):
    out=rows[:]
    if len(out) >= 2:
        out[0]=''.join(str(int(a)^int(b)) for a,b in zip(out[0], out[1]))
        out[0],out[1]=out[1],out[0]
    return out


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
        self.assertEqual(rec['last_excluded_weight'], 0)

    def test_record_tampering_and_replay(self):
        m=audit.load_manifest(); c=audit.case_map(m)['hnrv1-c0020']; rec=audit.audit_case(c,6)
        audit.validate_record(rec,m,True)
        bad=copy.deepcopy(rec); bad['raw_H_sha256']='0'*64; bad['record_sha256']=audit.sha({k:v for k,v in bad.items() if k!='record_sha256'})
        with self.assertRaises(ValueError): audit.validate_record(bad,m,False)
        bad=copy.deepcopy(rec); bad['witness_support']=[1,2,3]; bad['record_sha256']=audit.sha({k:v for k,v in bad.items() if k!='record_sha256'})
        with self.assertRaises(ValueError): audit.validate_record(bad,m,False)
        with self.assertRaises(ValueError): audit.ordered_split_search(c['H_rows'], True)

    def test_xorshift_structural_relation(self):
        for n,r,seed in [(128,48,7707),(144,72,9909),(160,80,3030)]:
            self.assertTrue(audit.dense_xorshift_relation_holds(n,r,seed)['holds'])

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
        self.assertEqual(boundary['last_excluded_weight'], 1)
        self.assertEqual(boundary['per_weight'][0]['outcome'], 'EXHAUSTED_NO_WITNESS')
        during_left=audit.ordered_split_search(rows, 2, interrupt_after_subsets=9+8+1)
        self.assertEqual(during_left['per_weight'][-1]['outcome'], 'INTERRUPTED')
        self.assertEqual(during_left['per_weight'][-1]['examined_left_subsets'], 1)

    def test_canonical_jsonl_rejections_and_summary_refuses_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            path=audit.write_audit(6,d)
            recs=audit.read_canonical_jsonl(path)
            self.assertEqual(len(recs), 12)

            with open(path,'rb') as f:
                data=f.read()
            for suffix in [b'\n', b'  \n']:
                bad=os.path.join(d,'bad.jsonl')

                with open(bad,'wb') as f:
                    f.write(data.splitlines()[0]+suffix+data.splitlines()[1:][0]+b'\n')
                with self.assertRaises(ValueError): audit.read_canonical_jsonl(bad)
            bad=os.path.join(d,'dupe.jsonl')
            line=data.splitlines()[0].decode()

            with open(bad,'w',encoding='utf-8') as f:
                f.write(line[:-1]+',"case_id":"hnrv1-c0012"}\n')
            with self.assertRaises(ValueError): audit.read_canonical_jsonl(bad)
            tampered=copy.deepcopy(recs[0]); tampered['status']='CERTIFIED_LOWER_BOUND'; tampered['record_sha256']=audit.sha({k:v for k,v in tampered.items() if k!='record_sha256'})
            bad=os.path.join(d,'tamper.jsonl')

            with open(bad,'w',encoding='utf-8') as f:
                f.write(audit.canonical_json_bytes(tampered).decode()+'\n')
            with self.assertRaises(ValueError): audit.main(['summary', bad])

    def test_preflight_rank_redundant_rows(self):
        rows=['1100','1100','0000']
        pf=audit.preflight_for_rows(rows,2)
        self.assertEqual(pf['rank_H'], 1)
        self.assertLessEqual(pf['weights'][0]['peak_map_bound'], 2)

    def test_schema_rejects_missing_witness_and_inconsistent_bounds(self):
        m=audit.load_manifest(); c=audit.case_map(m)['hnrv1-c0020']; rec=audit.audit_case(c,6)
        bad=copy.deepcopy(rec); bad.pop('witness_support'); bad['record_sha256']=audit.sha({k:v for k,v in bad.items() if k!='record_sha256'})
        with self.assertRaises(ValueError): audit.validate_record(bad,m)
        bad=copy.deepcopy(rec); bad['certified_bound']=4; bad['record_sha256']=audit.sha({k:v for k,v in bad.items() if k!='record_sha256'})
        with self.assertRaises(ValueError): audit.validate_record(bad,m)

    def test_xorshift_inapplicable_is_explicit_true(self):
        self.assertFalse(audit.dense_xorshift_relation_holds(10,8,1)['applicable'])


    def test_resource_limit_config_and_count_tampering(self):
        m=audit.load_manifest(); c=audit.case_map(m)['hnrv1-c0020']
        rec=audit.audit_case(c,6,0)
        audit.validate_record(rec,m,True)
        for value in [None, rec['audit_config']['subset_visit_limit'] + 1]:
            bad=copy.deepcopy(rec); bad['audit_config']['subset_visit_limit']=value; bad['audit_config_sha256']=audit.sha(bad['audit_config']); resign(bad)
            with self.assertRaises(ValueError): audit.validate_record(bad,m)
        bad=copy.deepcopy(rec); bad['per_weight'][0]['examined_right_subsets']=1; resign(bad)
        with self.assertRaises(ValueError): audit.validate_record(bad,m)
        exact=audit.audit_case(c,6)
        bad=copy.deepcopy(exact); bad['audit_config']['subset_visit_limit']=0; bad['audit_config_sha256']=audit.sha(bad['audit_config']); resign(bad)
        with self.assertRaises(ValueError): audit.validate_record(bad,m)
        ok=copy.deepcopy(exact); total=sum(w['examined_right_subsets']+w['examined_left_subsets'] for w in ok['per_weight']); ok['audit_config']['subset_visit_limit']=total; ok['audit_config_sha256']=audit.sha(ok['audit_config']); resign(ok)
        audit.validate_record(ok,m)

    def test_syndrome_map_size_cannot_exceed_examined_right(self):
        m=audit.load_manifest(); c=audit.case_map(m)['hnrv1-c0020']; rec=audit.audit_case(c,6,0)
        bad=copy.deepcopy(rec); bad['per_weight'][0]['syndrome_map_size']=1; resign(bad)
        with self.assertRaises(ValueError): audit.validate_record(bad,m)

    def test_genuine_invertible_row_operations_preserve_support(self):
        rows=h_from_support(12, [2,4,5,9,10])
        ops=invertible_row_ops(rows)
        rec=audit.ordered_split_search(ops,6)
        self.assertEqual(rec['witness_support'], [2,4,5,9,10])

    def test_representative_retention_required_for_ordered_completion(self):
        # Same-syndrome right halves for L=(2,): (1,3) fails ordering, (3,4) preserves it.
        rows=['00110','00000']
        cols,_,_=audit.parse_h_rows(rows)
        self.assertEqual(audit.syndrome(cols,(1,3)), audit.syndrome(cols,(3,4)))
        self.assertEqual(audit.syndrome(cols,(2,)), audit.syndrome(cols,(3,4)))
        self.assertFalse(min((1,3)) > 2)
        self.assertTrue(min((3,4)) > 2)
        self.assertTrue(audit.representative_better((3,4),(1,3)))

    def test_extended_canonical_jsonl_rejections(self):
        with tempfile.TemporaryDirectory() as d:
            path=audit.write_audit(6,d)
            with open(path,'rb') as f: data=f.read()
            lines=data.splitlines()
            cases=[('nonewline', data.rstrip(b'\n')), ('blank', lines[0]+b'\n\n'+b'\n'.join(lines[1:])+b'\n'), ('space', lines[0]+b' \n'+b'\n'.join(lines[1:])+b'\n'), ('utf8', b'\xff\n')]
            for name, payload in cases:
                bad=os.path.join(d,name+'.jsonl')
                with open(bad,'wb') as f: f.write(payload)
                with self.assertRaises(ValueError): audit.read_canonical_jsonl(bad)
            rec=json.loads(lines[0].decode())
            reordered='{"record_sha256":"%s","audit_protocol_version":"%s"}\n' % (rec['record_sha256'], rec['audit_protocol_version'])
            bad=os.path.join(d,'reordered.jsonl')
            with open(bad,'w',encoding='utf-8') as f: f.write(reordered)
            with self.assertRaises(ValueError): audit.read_canonical_jsonl(bad)
            for const in ['NaN','Infinity']:
                bad=os.path.join(d,const+'.jsonl')
                with open(bad,'w',encoding='utf-8') as f: f.write('{"x":%s}\n' % const)
                with self.assertRaises(ValueError): audit.read_canonical_jsonl(bad)
            # missing and duplicate cases
            bad=os.path.join(d,'missing.jsonl')
            with open(bad,'wb') as f: f.write(b'\n'.join(lines[:-1])+b'\n')
            with self.assertRaises(ValueError): audit.read_canonical_jsonl(bad)
            bad=os.path.join(d,'duplicate_case.jsonl')
            with open(bad,'wb') as f: f.write(lines[0]+b'\n'+b'\n'.join(lines)+b'\n')
            with self.assertRaises(ValueError): audit.read_canonical_jsonl(bad)

    def test_protected_field_tampering_matrix(self):
        m=audit.load_manifest(); c=audit.case_map(m)['hnrv1-c0020']; rec=audit.audit_case(c,6)
        mutations=[
            lambda r: r.__setitem__('audit_protocol_version','bad'),
            lambda r: r.__setitem__('corpus_protocol_version','bad'),
            lambda r: r.__setitem__('manifest_sha256','0'*64),
            lambda r: r.__setitem__('audit_algorithm','bad'),
            lambda r: r.__setitem__('case_id','hnrv1-c0012'),
            lambda r: r.__setitem__('requested_cap',5),
            lambda r: r['audit_config'].__setitem__('max_weight',5),
            lambda r: r['per_weight'][0].__setitem__('examined_right_subsets', False),
            lambda r: r['per_weight'][-1].__setitem__('outcome','EXHAUSTED_NO_WITNESS'),
            lambda r: r.__setitem__('preflight',{}),
            lambda r: r.__setitem__('witness_support', r['witness_support'][:-1]),
            lambda r: r.__setitem__('certified_bound', r['certified_bound']+1),
        ]
        for mutate in mutations:
            bad=copy.deepcopy(rec); mutate(bad); bad['audit_config_sha256']=audit.sha(bad.get('audit_config',{})); resign(bad)
            with self.assertRaises(ValueError): audit.validate_record(bad,m)

    def test_cli_missing_file_and_malformed_return_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            missing=os.path.join(d,'missing.jsonl')
            import subprocess, sys
            proc=subprocess.run([sys.executable,'rlmw_research_corpus_audit.py','validate',missing], cwd=os.getcwd(), text=True, capture_output=True)
            self.assertNotEqual(proc.returncode,0)
            self.assertIn('No such file', proc.stderr)
            proc=subprocess.run([sys.executable,'rlmw_research_corpus_audit.py','summary'], cwd=os.getcwd(), text=True, capture_output=True)
            self.assertNotEqual(proc.returncode,0)
            self.assertIn('usage:', proc.stderr)

    def test_cli_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path=audit.write_audit(6,d)
            audit.main(['validate', path])
            audit.main(['validate', path, '--replay-exclusion'])

if __name__ == '__main__':
    unittest.main()
