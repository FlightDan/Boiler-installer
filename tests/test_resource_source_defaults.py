import unittest
from protocol.resource_selection import _choices


def resource(identity, dependencies=(), files=True):
    return dict(id=identity, version='1', dependencies=list(dependencies),
        install=dict(kind='files-v1', files=[dict(scope='root-slots')] if files else []))


class ResourceSourceDefaultsTests(unittest.TestCase):
    def test_defaults_are_suppressed_for_unavailable_and_dependents(self):
        pool=dict(resources=[resource('resource:a', files=False),
            resource('resource:b', ['resource:a']), resource('resource:c')])
        model=dict(resources=[dict(resource='resource:a', default=True),
            dict(resource='resource:b', default=True), dict(resource='resource:c', default=True, optional=True)])
        rows={row['id']:row for row in _choices(pool,model)}
        self.assertFalse(rows['resource:a']['default'])
        self.assertFalse(rows['resource:b']['default'])
        self.assertTrue(rows['resource:c']['default'])
        self.assertTrue(rows['resource:c']['optional'])
    def test_legacy_rows_default_to_optional_unselected(self):
        row=_choices(dict(resources=[resource('resource:a')]),
            dict(resources=[dict(resource='resource:a')]))[0]
        self.assertFalse(row['default']);self.assertTrue(row['optional'])


if __name__ == '__main__':unittest.main()
