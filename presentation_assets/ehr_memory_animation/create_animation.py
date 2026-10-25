"""Render the repository's EHR memory workflow as a presentation video.

Uses Pillow for typography/vector-style graphics and imageio-ffmpeg for encoding.
Run: python create_animation.py
"""
from pathlib import Path
import math
import subprocess

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
W, H, FPS, SECONDS = 1920, 1080, 24, 7
BG = '#F4F7FB'
NAVY = '#0C1729'
TEAL = '#008B83'
MINT = '#90E0D1'
BLUE = '#477FD0'
INK = '#18263A'
MUTED = '#61738C'
LINE = '#D9E3EF'
PALE = '#E9F2FF'
RED = '#D9586A'
RED_BG = '#FCEEF1'
GREEN_BG = '#E4F5EF'
WHITE = '#FFFFFF'
REG = '/System/Library/Fonts/Supplemental/Arial.ttf'
BOLD = '/System/Library/Fonts/Supplemental/Arial Bold.ttf'
MONO = '/System/Library/Fonts/Menlo.ttc'
FONT_CACHE = {}


def font(size, weight='regular'):
    key = size, weight
    if key not in FONT_CACHE:
        FONT_CACHE[key] = ImageFont.truetype(
            {'regular': REG, 'bold': BOLD, 'mono': MONO}[weight], size)
    return FONT_CACHE[key]


def txt(draw, xy, text, size=30, fill=INK, weight='regular', width=None, spacing=11):
    face = font(size, weight)
    lines = []
    for paragraph in text.split('\n'):
        if width is None or not paragraph:
            lines.append(paragraph)
            continue
        line = ''
        for word in paragraph.split():
            candidate = (line + ' ' + word).strip()
            if draw.textlength(candidate, font=face) > width and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    draw.multiline_text(xy, '\n'.join(lines), font=face, fill=fill, spacing=spacing)
    return len(lines) * (size + spacing)


def box(d, rect, fill=WHITE, outline=None, radius=25):
    d.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=2)


def arrow(d, start, end, color=TEAL, width=5):
    d.line([start, end], fill=color, width=width)
    a = math.atan2(end[1]-start[1], end[0]-start[0])
    pts = [end]
    for sign in [-1, 1]:
        pts.append((end[0]-18*math.cos(a+sign*.48), end[1]-18*math.sin(a+sign*.48)))
    d.polygon(pts, fill=color)


def label(d, xy, text, fill=TEAL):
    txt(d, xy, text, 23, fill, 'bold')


LABELS = ['TXT log', 'Memory records', 'Retrieval', 'Checker', 'Generation', 'Later lessons', 'Overview']
TITLES = [
    ('A text log becomes reusable experience', 'A historical transcript records a task, worked examples, code attempts, feedback, and results.'),
    ('Parse the log into memory records', 'Each example supplies a question, supporting knowledge, and a solution field.'),
    ('Find examples for the current question', 'Question similarity selects examples; code similarity finds previously stored lessons.'),
    ('Check each example before using it', 'Generate one reasoning description per example, then judge them together in one LLM call.'),
    ('Build the generation prompts', 'Accepted examples and previously retrieved lessons guide knowledge and code generation.'),
    ('A rejection can help a later question', 'Store the rejected reasoning on its source record; retrieve it later through code similarity.'),
    ('From a past example to a new solution', 'Examples suggest methods. The checker filters. Historical lessons warn later questions.'),
]


def base(scene):
    im = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(im)
    label(d, (100, 43), 'A-MEMGUARD  /  EHRAGENT MEMORY WORKFLOW')
    txt(d, (100, 101), TITLES[scene][0], 59, NAVY, 'bold')
    txt(d, (103, 184), TITLES[scene][1], 28, MUTED, width=1710)
    d.line([(100, 929), (1820, 929)], fill=LINE, width=2)
    xs = [160+i*(1600/6) for i in range(7)]
    for i in range(6):
        d.line([(xs[i]+20, 969), (xs[i+1]-20, 969)], fill=TEAL if i < scene else LINE, width=4)
    for i, x in enumerate(xs):
        d.ellipse((x-17, 952, x+17, 986), fill=TEAL if i <= scene else LINE)
        text = str(i+1)
        d.text((x-d.textlength(text,font=font(20,'bold'))/2,956),text,font=font(20,'bold'),fill=WHITE if i<=scene else MUTED)
        f = font(21, 'bold' if i == scene else 'regular')
        d.text((x-d.textlength(LABELS[i],font=f)/2,1000),LABELS[i],font=f,fill=TEAL if i==scene else MUTED)
    txt(d, (100, 1047), 'Adapted EHR evaluation  •  Generated database code is not executed', 18, MUTED)
    txt(d, (1670, 1047), f'{scene+1:02d} / 07', 18, MUTED)
    return im


SCENES = []


def layer(layers, onset, painter):
    im = Image.new('RGBA', (W, H))
    painter(ImageDraw.Draw(im))
    bounds = im.getbbox()
    layers.append((onset, bounds, im.crop(bounds)))


def make_scene(scene):
    layers = []
    if scene == 0:
        def document(d):
            box(d, (100, 295, 1190, 860), NAVY)
            label(d,(140,327),'SOURCE FILE  /  HISTORICAL .TXT LOG',MINT)
            txt(d,(140,388),'Question',24,MINT,'bold')
            txt(d,(140,428),'What were the top four frequent\ndiagnoses until 2103?',37,WHITE,'mono')
            d.line([(140,547),(1145,547)],fill='#2A3A50',width=2)
            txt(d,(140,577),'Knowledge',24,MINT,'bold')
            txt(d,(140,620),'Use diagnosis records.\nCount diagnosis frequency.',32,WHITE,'mono')
            txt(d,(140,746),'Solution  →  feedback  →  revision',29,MINT,'mono')
        layer(layers,.1,document)
        def contents(d):
            label(d,(1260,321),'WHAT THE LOG CONTAINS')
            for i, s in enumerate(['Task + answer summary','Database / tool instructions','Four worked examples','Generated code + error feedback','Recorded result + ground truth']):
                y=386+i*83
                d.ellipse((1261,y+7,1273,y+19),fill=TEAL)
                txt(d,(1295,y),s,28,INK,width=475)
        layer(layers,1.1,contents)
    elif scene == 1:
        def source(d):
            box(d,(100,390,430,705),PALE)
            label(d,(138,430),'TEXT LOGS')
            for i in range(4):
                d.line([(140,498+i*33),(380-i*18,498+i*33)],fill=BLUE,width=7)
            txt(d,(138,648),'Parse examples',27,BLUE,'bold')
        layer(layers,.1,source)
        layer(layers,.65,lambda d:arrow(d,(452,545),(530,545)))
        def record(d):
            box(d,(550,300,1270,800),NAVY)
            label(d,(590,338),'ONE STRUCTURED MEMORY RECORD',MINT)
            for y,k,v in [(414,'question','What was asked?'),(526,'knowledge','Which tables and relationships?'),(638,'code','The recorded solution text')]:
                txt(d,(590,y),k,29,MINT,'mono')
                txt(d,(590,y+43),v,30,WHITE)
            txt(d,(590,743),'+ record ID and source metadata',22,'#A5B6CE')
        layer(layers,1,record)
        layer(layers,1.7,lambda d:arrow(d,(1290,545),(1360,545)))
        def pool(d):
            box(d,(1380,350,1820,740),WHITE,LINE)
            label(d,(1420,389),'ATTACK MEMORY POOL')
            txt(d,(1420,449),'51',76,NAVY,'bold')
            txt(d,(1541,481),'original',29,MUTED)
            txt(d,(1420,551),'+ 4',64,RED,'bold')
            txt(d,(1553,575),'poisoned',28,RED)
            txt(d,(1420,675),'55 records in total',29,INK,'bold')
        layer(layers,2.1,pool)
        layer(layers,2.8,lambda d:txt(d,(103,846),'The solution field can retain code attempts and debugging feedback from the source log.',27,MUTED))
    elif scene == 2:
        def query(d):
            box(d,(100,287,1820,427),PALE)
            label(d,(137,312),'ILLUSTRATIVE NEW QUESTION')
            txt(d,(137,356),'What were the top five frequent diagnoses until 2104?',38,NAVY,'bold')
        layer(layers,.1,query)
        def search(d):
            box(d,(100,486,555,747),NAVY)
            label(d,(140,520),'MEMORY QUESTION EMBEDDINGS',MINT)
            txt(d,(140,579),'Compare similarity\nwith the new question',34,WHITE)
            txt(d,(140,695),'Select the nearest examples',24,'#A5B6CE')
            arrow(d,(578,612),(735,612))
            txt(d,(591,559),'Top 4',26,TEAL,'bold')
        layer(layers,.8,search)
        for i in range(4):
            def example(d,i=i):
                x=775+(i%2)*535;y=477+(i//2)*152
                box(d,(x,y,x+510,y+129),WHITE,LINE)
                label(d,(x+28,y+24),f'EXAMPLE {i+1}')
                txt(d,(x+28,y+69),'Question + knowledge + code',25,INK)
            layer(layers,1.3+i*.25,example)
        def prior(d):
            box(d,(100,795,1820,882),GREEN_BG)
            txt(d,(134,821),'Before checking: collect existing lessons through the examples’ code neighbors.',29,TEAL,'bold')
        layer(layers,2.7,prior)
    elif scene == 3:
        for i in range(4):
            def chain(d,i=i):
                y=305+i*113
                box(d,(100,y,650,y+92),WHITE,LINE)
                txt(d,(125,y+29),f'Example {i+1}  →  reasoning {i+1}',30,INK,'bold')
            layer(layers,.1+i*.22,chain)
        def judge(d):
            arrow(d,(672,513),(744,513))
            box(d,(765,386,1195,649),NAVY)
            label(d,(805,427),'ONE JOINT LLM JUDGMENT',MINT)
            txt(d,(805,490),'Consistent?\nSafe?',40,WHITE,'bold')
            txt(d,(805,603),'Accept only if both are true',24,'#A5B6CE')
        layer(layers,1.15,judge)
        def decisions(d):
            arrow(d,(1210,472),(1300,396))
            arrow(d,(1210,570),(1300,594),RED)
            box(d,(1320,305,1820,471),GREEN_BG)
            label(d,(1354,336),'ACCEPTED EXAMPLES')
            txt(d,(1354,388),'Keep in ordinary context',29,INK)
            box(d,(1320,510,1820,676),RED_BG)
            label(d,(1354,541),'REJECTED EXAMPLES',RED)
            txt(d,(1354,593),'Remove from example context',28,INK)
        layer(layers,2,decisions)
        def store(d):
            arrow(d,(1570,688),(1570,736),RED)
            box(d,(750,755,1820,868),PALE)
            txt(d,(782,774),'When lessons are enabled:',24,BLUE,'bold')
            txt(d,(782,814),'Save rejected reasoning on its source memory record.',28,INK)
            txt(d,(103,817),'A rejection is an LLM judgment.\nIt does not establish an executed failure.',24,MUTED)
        layer(layers,2.9,store)
    elif scene == 4:
        def accepted(d):
            box(d,(100,305,695,508),GREEN_BG)
            label(d,(137,340),'ACCEPTED EXAMPLES')
            txt(d,(137,398),'Question + knowledge + solution',30,INK,width=515)
        layer(layers,.1,accepted)
        def warning(d):
            box(d,(100,552,695,774),PALE)
            label(d,(137,587),'PREVIOUSLY RETRIEVED LESSONS',BLUE)
            txt(d,(137,640),'Warning + past reasoning\nabout mistakes to avoid',31,INK)
        layer(layers,.7,warning)
        def knowledge(d):
            arrow(d,(712,406),(821,480))
            arrow(d,(712,661),(821,557))
            box(d,(844,365,1265,732),NAVY)
            label(d,(880,406),'GENERATE KNOWLEDGE',MINT)
            txt(d,(880,473),'Relevant tables\nand relationships',35,WHITE,'bold')
            txt(d,(880,641),'Uses example questions\nand knowledge + warnings',23,'#A5B6CE')
        layer(layers,1.35,knowledge)
        def code(d):
            arrow(d,(1284,547),(1360,547))
            box(d,(1380,365,1820,732),NAVY)
            label(d,(1417,406),'GENERATE CODE',MINT)
            txt(d,(1417,473),'Propose a solution\nfor the new task',35,WHITE,'bold')
            txt(d,(1417,641),'Also receives accepted\nsolution examples + warnings',23,'#A5B6CE')
        layer(layers,2.1,code)
        def note(d):
            box(d,(100,815,1820,889),WHITE,LINE)
            txt(d,(130,837),'New lessons created during this check become available to later questions.',29,MUTED)
        layer(layers,2.8,note)
    elif scene == 5:
        def next_task(d):
            box(d,(100,315,615,583),PALE)
            label(d,(137,354),'A LATER QUESTION')
            txt(d,(137,415),'Retrieve its examples\nand inspect their code',35,NAVY,'bold')
            txt(d,(137,533),'The wording can be different.',25,MUTED)
        layer(layers,.1,next_task)
        def neighbors(d):
            arrow(d,(636,449),(725,449))
            box(d,(745,315,1225,583),NAVY)
            label(d,(782,354),'CODE SIMILARITY SEARCH',MINT)
            txt(d,(782,415),'Find neighboring\nmemory records',35,WHITE,'bold')
            txt(d,(782,533),'Collect lessons where present.',25,'#A5B6CE')
        layer(layers,.9,neighbors)
        def lesson(d):
            arrow(d,(1245,449),(1330,449))
            box(d,(1350,315,1820,755),GREEN_BG)
            label(d,(1386,354),'WARNING IN THE PROMPT')
            txt(d,(1386,422),'Past reasoning may\ncontain incorrect\nmethods or pitfalls.',32,INK)
            txt(d,(1386,582),'Avoid repeating\nthose mistakes.',35,TEAL,'bold')
            txt(d,(1386,697),'A warning grants no permissions.',22,MUTED)
        layer(layers,1.7,lesson)
        def stored(d):
            box(d,(100,644,1225,806),WHITE,LINE)
            label(d,(137,675),'STORED ON THE SOURCE RECORD')
            txt(d,(137,722),'memory_2.lesson = latest rejected reasoning',29,INK,'mono')
            txt(d,(103,846),'One lesson slot per record: a later rejection overwrites its previous lesson.',28,MUTED)
        layer(layers,2.6,stored)
    else:
        cards=[('01','TXT log','Historical task transcript'),('02','Memory record','Question + knowledge + code'),('03','Retrieval','Similar questions + prior lessons'),('04','Checker','Judge consistency and safety'),('05','Generation','Accepted examples guide new code'),('06','Later lessons','Rejected reasoning can warn again')]
        for i,(n,title,detail) in enumerate(cards):
            def card(d,i=i,n=n,title=title,detail=detail):
                col=i%3;row=i//3;x=100+col*585;y=300+row*237
                box(d,(x,y,x+550,y+200),NAVY if i in (3,4) else WHITE, None if i in(3,4) else LINE)
                dark=i in(3,4)
                txt(d,(x+30,y+29),n,26,MINT if dark else TEAL,'bold')
                txt(d,(x+30,y+73),title,38,WHITE if dark else NAVY,'bold')
                txt(d,(x+30,y+133),detail,25,'#A5B6CE' if dark else MUTED,width=488)
            layer(layers,.1+i*.3,card)
        layer(layers,2.4,lambda d:txt(d,(104,824),'The current question uses prior lessons. New rejected reasoning is saved for future questions.',28,TEAL,'bold',width=1700))
    return base(scene), layers


def ease(x):
    return 1-(1-max(0,min(1,x)))**3


def render_scene(index,t):
    bg,layers=SCENES[index]
    im=bg.copy()
    for onset,bounds,picture in layers:
        alpha=ease((t-onset)/.55)
        if alpha <= 0:
            continue
        moving=picture
        if alpha<1:
            moving=picture.copy()
            moving.putalpha(picture.getchannel('A').point(lambda v:int(v*alpha)))
        im.paste(moving,(bounds[0],bounds[1]+int(12*(1-alpha))),moving)
    d=ImageDraw.Draw(im)
    # A quiet elapsed-time indicator, separate from the discrete step markers.
    d.rectangle((0,H-5,int(W*(index*SECONDS+t)/(len(LABELS)*SECONDS)),H),fill=TEAL)
    return im


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    SCENES.extend(make_scene(i) for i in range(7))
    encoder=imageio_ffmpeg.get_ffmpeg_exe()
    video=OUT/'ehr_memory_workflow.mp4'
    command=[encoder,'-y','-loglevel','error','-f','rawvideo','-vcodec','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-an','-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p','-movflags','+faststart',str(video)]
    process=subprocess.Popen(command,stdin=subprocess.PIPE)
    for index in range(7):
        last=render_scene(index,SECONDS-.01)
        last.save(OUT/f'step_{index+1:02d}.png')
        for frame in range(SECONDS*FPS):
            t=frame/FPS
            im=render_scene(index,t)
            if index and t<.25:
                im=Image.blend(render_scene(index-1,SECONDS-.01),im,ease(t/.25))
            process.stdin.write(im.tobytes())
        print(f'Rendered step {index+1}/7',flush=True)
    process.stdin.close()
    if process.wait():
        raise RuntimeError('Video encoding failed')
    render_scene(6,6).save(OUT/'ehr_memory_workflow_poster.png')
    print('Encoding looping GIF',flush=True)
    subprocess.run([encoder,'-y','-loglevel','error','-i',str(video),'-filter_complex','fps=10,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3','-loop','0',str(OUT/'ehr_memory_workflow.gif')],check=True)
    thumbs=Image.new('RGB',(1440,3*285),BG)
    for i in range(7):
        im=render_scene(i,6).resize((480,270),Image.Resampling.LANCZOS)
        thumbs.paste(im,((i%3)*480,(i//3)*285))
    thumbs.save(OUT/'storyboard.png')
    print('Complete:',video,flush=True)


if __name__=='__main__':
    main()
