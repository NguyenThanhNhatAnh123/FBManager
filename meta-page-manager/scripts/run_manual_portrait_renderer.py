"""Compose an unchanged portrait source with the pinned renderer's cover crop.

Manual-review runs may receive portrait native images whose ratio is not 9:16.
Only the aspect guard changes; the existing bounds, audio, camera expression,
decode and final 1080x1920 checks remain unchanged. No source pixels are edited.
"""
import ast
import hashlib
from pathlib import Path
import sys


RENDERER = Path(__file__).resolve().parents[2] / 'page-producer/.agents/skills/page-shirt-zoom-8s/scripts/render_video.py'
RENDERER_SHA256 = '9c00ea67a83c0d6eb61cef238f1c1de192486792317436ae91b1f4f7fd650144'
# Exact reviewed historical bytes remain valid for existing, frozen runs.
RENDERER_SHA256S = frozenset({RENDERER_SHA256,
    '9811628c2601ea73b32c02ce3ac73b6b9a36869ae228e42273dc6e915b18a8e5'})


def renderer_tree(raw):
    if hashlib.sha256(raw).hexdigest() not in RENDERER_SHA256S:
        raise ValueError('manual_renderer_configuration_changed')
    tree = ast.parse(raw, filename=str(RENDERER))
    expected = ast.dump(ast.parse("getattr(image, 'n_frames', 1) != 1 or abs(width / height - 9 / 16) > 0.001", mode='eval').body,
                        include_attributes=False)
    replacement = ast.parse("getattr(image, 'n_frames', 1) != 1 or not 0 < width < height or width * height > 40000000", mode='eval').body
    class PortraitComposition(ast.NodeTransformer):
        changed = 0
        def visit_If(self, node):
            self.generic_visit(node)
            if ast.dump(node.test, include_attributes=False) == expected:
                node.test = replacement
                self.changed += 1
            return node
    transform = PortraitComposition()
    tree = transform.visit(tree)
    if transform.changed != 1:
        raise ValueError('manual_renderer_configuration_changed')
    return ast.fix_missing_locations(tree)


def main(argv=None):
    raw = RENDERER.read_bytes()
    tree = renderer_tree(raw)
    sys.path.insert(0, str(RENDERER.parent))
    sys.argv = [str(RENDERER), *(sys.argv[1:] if argv is None else argv)]
    exec(compile(tree, str(RENDERER), 'exec'), {'__name__': '__main__', '__file__': str(RENDERER), '__package__': None})


if __name__ == '__main__':
    main()
