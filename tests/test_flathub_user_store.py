import contextlib,copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from maintenance import flathub_user_store as user_store
from test_flathub_activation import Fake


class SharedUserStoreTests(unittest.TestCase):
    def test_two_slots_retain_one_original_and_last_owner_restores(self):
        with tempfile.TemporaryDirectory() as temp:
            store=user_store.Store(temp);store.path.mkdir(parents=True);backend=Fake();before=copy.deepcopy(backend.values)
            with patch.object(store,'lock',return_value=contextlib.nullcontext()):
                store.acquire(backend,'a'*32,'a'*36,lambda _:None)
                store.acquire(backend,'a'*32,'b'*36,lambda _:None)
                self.assertEqual(store.release(backend,'a'*32,'a'*36,lambda _:None)['status'],'other-slot-retains')
                self.assertTrue(backend.values['user']['exists'])
                self.assertEqual(store.release(backend,'a'*32,'b'*36,lambda _:None)['status'],'restored')
            self.assertEqual(backend.values,before)

    def test_external_edit_is_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            store=user_store.Store(temp);store.path.mkdir(parents=True);backend=Fake()
            with patch.object(store,'lock',return_value=contextlib.nullcontext()):
                store.acquire(backend,'a'*32,'a'*36,lambda _:None)
                backend.values['user']['url']='https://external.invalid/'
                self.assertEqual(store.release(backend,'a'*32,'a'*36,lambda _:None)['status'],'user-changed-preserved')
                self.assertEqual(backend.values['user']['url'],'https://external.invalid/')

    def test_interrupted_create_only_original_slot_reconciles(self):
        with tempfile.TemporaryDirectory() as temp:
            store=user_store.Store(temp);store.path.mkdir(parents=True);backend=Fake();original=backend.create
            def interrupted(scope,url):original(scope,url);raise RuntimeError('kill after creation')
            with patch.object(store,'lock',return_value=contextlib.nullcontext()),patch.object(backend,'create',side_effect=interrupted):
                with self.assertRaisesRegex(RuntimeError,'kill'):store.acquire(backend,'a'*32,'a'*36,lambda _:None)
            with patch.object(store,'lock',return_value=contextlib.nullcontext()):
                with self.assertRaisesRegex(RuntimeError,'another slot'):store.acquire(backend,'a'*32,'b'*36,lambda _:None)
                state=store.acquire(backend,'a'*32,'a'*36,lambda _:None)
                self.assertEqual(state['owners'],['a'*36]);self.assertEqual(state['status'],'active')

    def test_second_slot_does_not_overwrite_user_edit(self):
        with tempfile.TemporaryDirectory() as temp:
            store=user_store.Store(temp);store.path.mkdir(parents=True);backend=Fake()
            with patch.object(store,'lock',return_value=contextlib.nullcontext()):
                store.acquire(backend,'a'*32,'a'*36,lambda _:None)
                backend.values['user']['url']='https://external.invalid/'
                with self.assertRaisesRegex(RuntimeError,'externally changed'):
                    store.acquire(backend,'a'*32,'b'*36,lambda _:None)
                self.assertEqual(backend.values['user']['url'],'https://external.invalid/')
                self.assertEqual(store.read()['owners'],['a'*36])


if __name__=='__main__':unittest.main()
