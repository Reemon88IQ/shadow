"""Build a tiny Blender test scene (runs INSIDE Blender; the tests call it, nothing is checked in as a .blend):
    blender -b --factory-startup --python tests/fixtures/make_blend_fixture.py -- out.blend [--fps 24] [--seconds 2]
        [--no-sound] [--no-vcam] [--missing-texture] [--engine eevee|cycles|workbench]
A cube that turns and changes colour over time (so frames differ), a sun light, a 16:9 camera "Camera" and, unless
--no-vcam, a 9:16 camera "Camera 9x16"; a 440 Hz sound strip (sound.wav written next to the .blend) unless --no-sound;
a timeline marker "thumbnail" at 1 s. --missing-texture adds an image texture whose file does not exist."""
import math, os, struct, sys, wave
import bpy

a = sys.argv[sys.argv.index('--') + 1:]
out = os.path.abspath(a[0]); opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
fps = int(opt('--fps', 24)); secs = float(opt('--seconds', 2))
bpy.ops.wm.read_factory_settings(use_empty=True)
s = bpy.context.scene
s.render.fps = fps; s.frame_start = 1; s.frame_end = int(round(fps * secs))
eng = opt('--engine', 'eevee')
for n in {'eevee': ['BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'], 'cycles': ['CYCLES'], 'workbench': ['BLENDER_WORKBENCH']}[eng]:
    try: s.render.engine = n; break
    except TypeError: pass
if s.render.engine == 'CYCLES': s.cycles.samples = 16
w = bpy.data.worlds.new('W'); s.world = w; w.color = (0.05, 0.06, 0.1)
bpy.ops.mesh.primitive_cube_add(size=1.6); cube = bpy.context.object
mat = bpy.data.materials.new('M')
if not mat.node_tree: mat.use_nodes = True   # Blender 5: materials have nodes already
cube.data.materials.append(mat)
bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
for f, col in ((s.frame_start, (1, 0.2, 0.1, 1)), (s.frame_end, (0.1, 0.4, 1, 1))):
    bsdf.inputs[0].default_value = col; bsdf.inputs[0].keyframe_insert('default_value', frame=f)
    cube.rotation_euler = (0.3, 0.2, math.pi * (f - 1) / max(1, s.frame_end - 1)); cube.keyframe_insert('rotation_euler', frame=f)
if '--missing-texture' in a:
    img = bpy.data.images.new('gone', 4, 4); img.source = 'FILE'; img.filepath = os.path.join(os.path.dirname(out), 'does_not_exist.png')
    t = mat.node_tree.nodes.new('ShaderNodeTexImage'); t.image = img
bpy.ops.object.light_add(type='SUN', location=(3, -3, 5)); bpy.context.object.data.energy = 4
bpy.ops.object.camera_add(location=(0, -6, 1.2), rotation=(math.radians(82), 0, 0)); cam = bpy.context.object; cam.name = 'Camera'
s.camera = cam
if '--no-vcam' not in a:
    bpy.ops.object.camera_add(location=(0, -8, 1.2), rotation=(math.radians(82), 0, 0)); bpy.context.object.name = 'Camera 9x16'
s.timeline_markers.new('thumbnail', frame=s.frame_start + fps)
if '--no-sound' not in a:
    wav = os.path.join(os.path.dirname(out), 'sound.wav')
    with wave.open(wav, 'wb') as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(48000)
        wf.writeframes(b''.join(struct.pack('<h', int(9000 * math.sin(2 * math.pi * 440 * i / 48000))) for i in range(int(48000 * secs))))
    se = s.sequence_editor_create()
    strips = se.strips if hasattr(se, 'strips') else se.sequences   # Blender 4.4+: strips (an empty collection is falsy)
    strips.new_sound('tone', wav, 1, s.frame_start)
bpy.ops.wm.save_as_mainfile(filepath=out)
print('SHADOW_FIXTURE', out, flush=True)
