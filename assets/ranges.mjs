export function parseRange(value,total=500){
 const match=/^\s*(\d{1,3})\s*[-–—]\s*(\d{1,3})\s*$/.exec(value);
 if(!match)throw Error('请输入序号范围，例如 0-24 或 450-474。');
 const start=Number(match[1]),end=Number(match[2]);
 if(start<0||end>=total||end-start!==24)throw Error('每组需要连续 25 张，序号范围为 0–499，例如 0-24 或 450-474。');
 return {start,end};
}
export function shiftRange(range,delta,total=500){
 const start=Math.max(0,Math.min(total-25,range.start+delta*25));
 return {start,end:start+24};
}
