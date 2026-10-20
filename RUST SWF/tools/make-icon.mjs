// Code-generated application mark; no external artwork.
import fs from 'node:fs';
import path from 'node:path';
const width=32,height=32;
const pixels=Buffer.alloc(width*height*4);
for(let y=0;y<height;y++)for(let x=0;x<width;x++){
  const inside=x>=3&&x<29&&y>=3&&y<29;
  const bracket=(x===9&&y>=10&&y<=21)||(x===22&&y>=10&&y<=21)||((y===10||y===21)&&((x>=9&&x<=12)||(x>=19&&x<=22)));
  const slash=x===Math.round(20-(y-9)*.55)&&y>=9&&y<=22;
  const color=bracket||slash?[15,41,34,255]:inside?[98,218,177,255]:[11,16,24,255];
  const p=((height-1-y)*width+x)*4;pixels[p]=color[2];pixels[p+1]=color[1];pixels[p+2]=color[0];pixels[p+3]=color[3];
}
const dib=Buffer.alloc(40);dib.writeUInt32LE(40,0);dib.writeInt32LE(width,4);dib.writeInt32LE(height*2,8);dib.writeUInt16LE(1,12);dib.writeUInt16LE(32,14);dib.writeUInt32LE(pixels.length,20);
const mask=Buffer.alloc(4*height);const image=Buffer.concat([dib,pixels,mask]);
const header=Buffer.alloc(22);header.writeUInt16LE(1,2);header.writeUInt16LE(1,4);header[6]=width;header[7]=height;header.writeUInt16LE(1,10);header.writeUInt16LE(32,12);header.writeUInt32LE(image.length,14);header.writeUInt32LE(22,18);
const folder=path.resolve('desktop/icons');fs.mkdirSync(folder,{recursive:true});fs.writeFileSync(path.join(folder,'icon.ico'),Buffer.concat([header,image]));

