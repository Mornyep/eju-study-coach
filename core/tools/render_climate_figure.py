"""Render an authored climate chart from exact data, never edit an official image."""
from pathlib import Path
import argparse, json, math
from PIL import Image, ImageDraw, ImageFont

def render(spec, destination, font):
    temperatures, rain = spec['temperature_c'], spec['precipitation_mm']
    assert len(temperatures) == len(rain) == 12
    assert all(type(x) in (int, float) and math.isfinite(x) and 0 <= x <= 40 for x in temperatures)
    assert all(type(x) in (int, float) and math.isfinite(x) and 0 <= x <= 100 for x in rain)
    image=Image.new('RGB',(1440,960),'white');d=ImageDraw.Draw(image)
    normal=ImageFont.truetype(str(font),30);small=ImageFont.truetype(str(font),27);title=ImageFont.truetype(str(font),38)
    d.text((720,50),'地点Yの月平均気温と月降水量',font=title,fill='#18252a',anchor='mt')
    d.text((720,112),'北半球・中緯度の架空の地点（学習用の仮定値）',font=small,fill='#445058',anchor='mt')
    left,right,top,bottom=155,1285,245,790
    d.text((left,175),'気温（℃）',font=normal,fill='#aa2b35',anchor='mt')
    d.text((right,175),'降水量（mm）',font=normal,fill='#17659d',anchor='mt')
    for i in range(6):
        y=bottom-(bottom-top)*i/5
        d.line((left,y,right,y),fill='#d2d8dc',width=2)
        d.text((left-22,y),str(i*8),font=small,fill='#aa2b35',anchor='rm')
        d.text((right+22,y),str(i*20),font=small,fill='#17659d',anchor='lm')
    d.line((left,top,left,bottom,right,bottom,right,top),fill='#253139',width=3)
    step=(right-left)/12;points=[]
    for i,(temp,precip) in enumerate(zip(temperatures,rain)):
        x=left+(i+.5)*step
        y=bottom-precip/100*(bottom-top)
        d.rectangle((x-step*.28,y,x+step*.28,bottom-2),fill='#75b7e1',outline='#17659d',width=2)
        points.append((x,bottom-temp/40*(bottom-top)))
        d.text((x,bottom+18),str(i+1),font=small,fill='#253139',anchor='mt')
    d.line(points,fill='#aa2b35',width=5)
    for x,y in points:d.ellipse((x-5,y-5,x+5,y+5),fill='#aa2b35')
    d.text((right+60,bottom+18),'月',font=small,fill='#253139',anchor='mt')
    d.line((370,890,420,890),fill='#aa2b35',width=5);d.text((435,890),'月平均気温',font=small,fill='#253139',anchor='lm')
    d.rectangle((785,877,825,903),fill='#75b7e1',outline='#17659d',width=2);d.text((840,890),'月降水量',font=small,fill='#253139',anchor='lm')
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True);image.save(destination)
    return {'width':1440,'height':960,'months':12,'temperature_axis':[0,40],'precipitation_axis':[0,100],'generated_from':spec}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('spec');p.add_argument('output');p.add_argument('--font',required=True);args=p.parse_args()
    print(json.dumps(render(json.loads(Path(args.spec).read_text()),args.output,args.font),ensure_ascii=False))
