"""Exercise every language/level's full practice and final-test question banks."""
from copy import deepcopy
import unittest
from flask import Flask
from learning import init_learning
from learning.services.content import get_levels, dose_catalog, content_store
from learning.services.progress import default_progress


class MemoryStore:
    def __init__(self): self.rows={}
    def load(self,user,language,default): return deepcopy(self.rows.get((user,language),default))
    def save(self,user,language,value): self.rows[(user,language)]=deepcopy(value)


class CompleteCurriculumTests(unittest.TestCase):
    def test_all_120_level_paths_produce_practice_and_final_tests(self):
        store=MemoryStore()
        app=Flask('complete_curriculum')
        app.config.update(TESTING=True,LEARNING_PROGRESS_STORE=store,LEARNING_LEARNER_ID_RESOLVER=lambda:'test')
        init_learning(app)
        client=app.test_client()
        for lang in ['es','fr','tl']:
            client.set_cookie('language',lang)
            with app.test_request_context(headers={'Cookie':'language='+lang}):
                levels=get_levels()['levels']
                catalog=dose_catalog()
                mapping=content_store().get_curriculum()
                self.assertEqual(len(levels),40)
                self.assertEqual(set(mapping),{level['id'] for level in levels})
                progress=default_progress()
            for level in levels:
                with self.subTest(language=lang,level=level['id']):
                    ids=mapping[level['id']]
                    self.assertEqual(len(set(ids)),10)
                    self.assertEqual(len({catalog[cid]['tagalog'] for cid in ids}),10)
                    self.assertTrue(all(catalog[cid]['accepted_answers'] for cid in ids))
                    state={'lesson_done':True,'practice_done':True,'passed':True,
                           'words':{cid:{'correct':3,'wrong':0,'recall_streak':3,'mastered':True,
                                         'misses':0,'support_correct':0} for cid in ids}}
                    progress['course'][level['id']]=state
                    progress['level_id']=level['id']
                    store.save('test',lang,progress)
                    self.assertEqual(client.get('/learn/'+level['id']).status_code,200)
                    self.assertEqual(client.get('/learn/'+level['id']+'/daily-dose').status_code,200)
                    # A fresh cycle is complete for this already-mastered test fixture.
                    current=store.rows[('test',lang)]
                    current['course'][level['id']]['dose_cycle']['index']=10
                    for mode in ['practice','final']:
                        response=client.post('/learn/'+level['id']+'/test',json={'action':'start','mode':mode})
                        self.assertEqual(response.status_code,200,response.get_data(as_text=True)[:200])
                        attempt=store.rows[('test',lang)]['course'][level['id']]['attempt']
                        self.assertEqual(len(attempt['questions']),20)
                        self.assertEqual(sum(q['kind']=='vocabulary' for q in attempt['questions']),10)
                        for q in attempt['questions']:
                            self.assertEqual(len(set(q['options'])),3)
                            self.assertIn(q['answer'],q['options'])
                    result=response.get_json()
                    questions=deepcopy(attempt['questions'])
                    for index, question in enumerate(questions):
                        data={'attempt':result['attempt'],'index':index}
                        self.assertEqual(client.post('/learn/'+level['id']+'/test',json=dict(data,action='ready')).status_code,200)
                        response=client.post('/learn/'+level['id']+'/test',json=dict(data,action='answer',choice=question['options'].index(question['answer'])))
                        self.assertEqual(response.status_code,200)
                        result=response.get_json()
                    self.assertEqual(result['score'],20)
                    self.assertTrue(result['passed'])
                    # Carry only the completed-level state into the next test fixture.
                    progress['course'][level['id']]['passed']=True
