"""Archive trust-boundary regression checks; no provider connections."""
import importlib.util
from io import BytesIO
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
import zipfile

script = Path(__file__).resolve().parents[1] / 'scripts/extract_linguavell_modal.py'
spec = importlib.util.spec_from_file_location('linguavell_extractor', script)
extractor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extractor)


def archive(entries):
    stream = BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(stream, 'w') as z:
            for name, content in entries:
                z.writestr(name, content)
    return stream.getvalue()


class ArchiveTests(unittest.TestCase):
    def test_entire_archive_rejected_before_any_writes(self):
        for bad in ['../escape.md', '/absolute.md', 'C:/outside.md', 'auto\\escape.md', 'script.py']:
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    extractor.safe_unpack(archive([('safe.md', '# safe'), (bad, 'x')]), Path(folder))
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_duplicate_paths_and_symlinks_rejected(self):
        link = zipfile.ZipInfo('auto/link.md')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for entries in [[('same.md', 'a'), ('same.md', 'b')], [(link, '/etc/passwd')]]:
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    extractor.safe_unpack(archive(entries), Path(folder))
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_expanded_size_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                extractor.safe_unpack(archive([('large.md', 'x' * 101)]), Path(folder), max_bytes=100)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_nested_markdown_and_image_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            md = extractor.safe_unpack(archive([('auto/book.md', '# Deutsch'),
                                                 ('auto/images/a.png', b'fixture')]), Path(folder))
            self.assertEqual(md.read_text(), '# Deutsch')
            self.assertEqual((Path(folder) / 'auto/images/a.png').read_bytes(), b'fixture')

    def test_existing_output_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'book.md'
            p.write_text('original')
            with self.assertRaises(FileExistsError):
                extractor.safe_unpack(archive([('book.md', 'replacement')]), Path(folder))
            self.assertEqual(p.read_text(), 'original')

    def test_invalid_or_ambiguous_markdown_not_success(self):
        for entries in [[('a.md', '')], [('a.md', 'x'), ('b.md', 'y')], [('image.png', b'fixture')]]:
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    extractor.safe_unpack(archive(entries), Path(folder))


if __name__ == '__main__':
    unittest.main()
