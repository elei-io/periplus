"""Evidence limits and CTE lineage for the shared-HTML-input warning."""
import json
import unittest

from periplus.query.scope_plan import shared_html_inputs


def node(name, info=None, *children):
    return dict(name=name, extra_info=info or {}, children=list(children))


class ScopePlanTests(unittest.TestCase):
    def plan(self, producer):
        keys = node('CTE', {'CTE Name': 'selected_keys', 'Table Index': '1'},
                    node('EMPTY_RESULT'), node('CTE_SCAN', {'CTE Index': '1'}))
        common = node('CTE', {'CTE Name': '__common_subplan_1', 'Table Index': '2'},
                      producer, node('CTE_SCAN', {'CTE Index': '1'}))
        return json.dumps([keys, common])

    def test_only_the_shared_producer_is_inspected(self):
        plan = self.plan(node('DUCKLAKE_SCAN', {'Table': 'html_nodes'}))
        self.assertEqual(shared_html_inputs(plan, key_cte='selected_keys'), ('html_nodes',))
        # A consumer key restriction does not constrain CTE construction.
        self.assertIsNone(shared_html_inputs(plan, key_cte='different_keys'))

    def test_restricted_and_non_html_producers_do_not_warn(self):
        producers = [
            node('HASH_JOIN', {}, node('DUCKLAKE_SCAN', {'Table': 'html_nodes'}),
                 node('CTE_SCAN', {'CTE Index': '1'})),
            node('DUCKLAKE_SCAN', {'Table': 'html_nodes', 'Filters': "content_sha256='a'"}),
            node('SEQ_SCAN', {'Table': 'memory.material.html_nodes', 'Dynamic Filters': 'content_id=a'}),
            node('DUCKLAKE_SCAN', {'Table': 'visits'}),
        ]
        for producer in producers:
            self.assertEqual(shared_html_inputs(self.plan(producer), key_cte='selected_keys'), ())

    def test_indirect_cte_dependencies_and_unrecognized_references(self):
        plan = json.loads(self.plan(node('CTE_SCAN', {'CTE Index': '3'})))
        self.assertIsNone(shared_html_inputs(json.dumps(plan), key_cte='selected_keys'))
        plan.append(node('CTE', {'CTE Name': 'intermediate', 'Table Index': '3'},
                         node('DUCKLAKE_SCAN', {'Table': 'html_nodes'}), node('EMPTY_RESULT')))
        self.assertEqual(shared_html_inputs(json.dumps(plan), key_cte='selected_keys'), ('html_nodes',))
        plan[-1]['children'][0] = node('CTE_SCAN', {'CTE Index': '1'})
        self.assertEqual(shared_html_inputs(json.dumps(plan), key_cte='selected_keys'), ())

    def test_bounded_and_unavailable_evidence(self):
        for raw in ('not json', '{}', '[]', ' ' * 1_000_001, json.dumps([{}]),
                    json.dumps([node('EMPTY_RESULT')] * 4097)):
            self.assertIsNone(shared_html_inputs(raw, key_cte='keys'))
        self.assertEqual(shared_html_inputs(json.dumps([node('EMPTY_RESULT')]), key_cte='keys'), ())
