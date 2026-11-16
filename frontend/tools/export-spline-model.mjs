/** Offline asset conversion. Usage: node tools/export-spline-model.mjs <scene.splinecode>
 * Preserves author Draco streams, mesh IDs, vertex topology and node transforms.
 * No script or event from the supplied scene is executed.
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { unpack, addExtension } from 'msgpackr';
import draco3d from 'draco3d';
import { Matrix4, Vector3, Quaternion, Euler, Color } from 'three';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const input=process.argv[2];if(!input)throw Error('Provide the authorized scene.splinecode path');
for(let type=1;type<=6;type++)addExtension({type,read:value=>value});
const source=fs.readFileSync(input),scene=unpack(source);
const decoderModule=await draco3d.createDecoderModule({});
const decoder=new decoderModule.Decoder();
const gltf={asset:{version:'2.0',generator:'Yuntong offline Spline geometry converter'},scene:0,scenes:[{nodes:[]}],nodes:[],meshes:[],materials:[],accessors:[],bufferViews:[],buffers:[{byteLength:0}],extensionsUsed:['KHR_draco_mesh_compression'],extensionsRequired:['KHR_draco_mesh_compression']};
const chunks=[];let offset=0,triangles=0,points=0;const worldBox={min:[Infinity,Infinity,Infinity],max:[-Infinity,-Infinity,-Infinity]};
const values=value=>Object.values(value||{});
const array=(value,fallback)=>value?values(value):fallback;
const pushAccessor=data=>(gltf.accessors.push(data),gltf.accessors.length-1);
function appendBinary(bytes){const view=gltf.bufferViews.push({buffer:0,byteOffset:offset,byteLength:bytes.length})-1;chunks.push(bytes);offset+=bytes.length;const padding=(4-offset%4)%4;if(padding){chunks.push(Buffer.alloc(padding));offset+=padding;}return view;}
function addNode(record,parentMatrix=new Matrix4()){
 const data=record.data;if(!data)return null;
 const position=array(data.position,[0,0,0]),rotation=array(data.rotation,[0,0,0]),scale=array(data.scale,[1,1,1]);
 const local=new Matrix4().compose(new Vector3(...position),new Quaternion().setFromEuler(new Euler(...rotation.map(value=>value*Math.PI/180),'XYZ')),new Vector3(...scale));
 if(data.hiddenMatrix)local.premultiply(new Matrix4().fromArray(values(data.hiddenMatrix)));
 const world=parentMatrix.clone().multiply(local);
 const node={name:data.name||data.type,matrix:local.toArray(),extras:{sourceId:record.id,sourceType:data.type}};
 const index=gltf.nodes.push(node)-1;
 if(data.type==='Mesh'&&data.visible!==false){
  const compressed=Buffer.from(values(data.geometry?.data?.draco));
  const buffer=compressed.length?new decoderModule.DecoderBuffer():null;if(buffer)buffer.Init(new Int8Array(compressed),compressed.length);
  const mesh=buffer?new decoderModule.Mesh():null,status=buffer?decoder.DecodeBufferToMesh(buffer,mesh):null;
  if(status&&!status.ok())throw Error(status.error_msg());
  const attributes={},dracoAttributes={};
  if(mesh)for(const [semantic,type] of [['POSITION',decoderModule.POSITION],['NORMAL',decoderModule.NORMAL],['TEXCOORD_0',decoderModule.TEX_COORD],['COLOR_0',decoderModule.COLOR]]){
   const id=decoder.GetAttributeId(mesh,type);if(id<0)continue;
   const attribute=decoder.GetAttribute(mesh,id),size=attribute.num_components();
   const accessor={componentType:5126,count:mesh.num_points(),type:'VEC'+size};
   if(semantic==='POSITION'){
    const bytes=mesh.num_points()*size*4,ptr=decoderModule._malloc(bytes);
    decoder.GetAttributeDataArrayForAllPoints(mesh,attribute,decoderModule.DT_FLOAT32,bytes,ptr);
    const positions=new Float32Array(decoderModule.HEAPF32.buffer,ptr,mesh.num_points()*size),min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity],point=new Vector3();
    for(let i=0;i<positions.length;i+=size){
     for(let axis=0;axis<3;axis++){min[axis]=Math.min(min[axis],positions[i+axis]);max[axis]=Math.max(max[axis],positions[i+axis]);}
     point.set(positions[i],positions[i+1],positions[i+2]).applyMatrix4(world);
     for(let axis=0;axis<3;axis++){const value=point.getComponent(axis);worldBox.min[axis]=Math.min(worldBox.min[axis],value);worldBox.max[axis]=Math.max(worldBox.max[axis],value);}
    }
    decoderModule._free(ptr);accessor.min=min;accessor.max=max;
   }
   attributes[semantic]=pushAccessor(accessor);dracoAttributes[semantic]=attribute.unique_id();
  }
  if(!mesh)for(const [key,attribute] of Object.entries(data.geometry.data.attributes)){
   const semantic={position:'POSITION',normal:'NORMAL',uv:'TEXCOORD_0',color:'COLOR_0'}[key];if(!semantic)continue;
   const raw=new Float32Array(values(attribute.array));const accessor={bufferView:appendBinary(Buffer.from(raw.buffer)),componentType:5126,count:raw.length/attribute.itemSize,type:'VEC'+attribute.itemSize};
   if(semantic==='POSITION'){
    const min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity],point=new Vector3();
    for(let i=0;i<raw.length;i+=3){for(let axis=0;axis<3;axis++){min[axis]=Math.min(min[axis],raw[i+axis]);max[axis]=Math.max(max[axis],raw[i+axis]);}point.fromArray(raw,i).applyMatrix4(world);for(let axis=0;axis<3;axis++){worldBox.min[axis]=Math.min(worldBox.min[axis],point.getComponent(axis));worldBox.max[axis]=Math.max(worldBox.max[axis],point.getComponent(axis));}}
    accessor.min=min;accessor.max=max;
   }
   attributes[semantic]=pushAccessor(accessor);
  }
  const materialSource=values(data.materials)[0],layers=values(materialSource?.layers).map(layer=>layer.data);
  const layer=[...layers].reverse().find(layer=>layer.type==='color'&&layer.visible!==false);
  if(!layer?.color)throw Error('Original color layer missing: '+data.name);
  // Spline serializes THREE.Color's linear components; do not apply sRGB twice.
  const color=new Color().setRGB(layer.color.r,layer.color.g,layer.color.b);
  const alpha=layer.alpha??1;
  const material={name:materialSource.name,pbrMetallicRoughness:{baseColorFactor:[color.r,color.g,color.b,alpha],metallicFactor:0,roughnessFactor:1},doubleSided:data.side===2,extras:{sourceColor:layer.color,sourceTextureAvailable:false}};
  if(alpha<1){material.alphaMode='BLEND';}
  const materialIndex=gltf.materials.push(material)-1;
  const primitive={attributes,material:materialIndex,mode:4};
  if(mesh){primitive.indices=pushAccessor({componentType:5125,count:mesh.num_faces()*3,type:'SCALAR'});primitive.extensions={KHR_draco_mesh_compression:{bufferView:appendBinary(compressed),attributes:dracoAttributes}};triangles+=mesh.num_faces();points+=mesh.num_points();decoderModule.destroy(mesh);decoderModule.destroy(buffer);decoderModule.destroy(status);}
  else{const count=gltf.accessors[attributes.POSITION].count;triangles+=count/3;points+=count;}
  node.mesh=gltf.meshes.push({name:data.name,primitives:[primitive]})-1;
 }
 const children=values(record.children).map(child=>addNode(child,world)).filter(index=>index!==null);if(children.length)node.children=children;
 return index;
}
gltf.scenes[0].nodes=values(scene.scene.objects).map(record=>addNode(record)).filter(index=>index!==null);
gltf.buffers[0].byteLength=offset;
const json=Buffer.from(JSON.stringify(gltf)),jsonPadding=(4-json.length%4)%4,jsonChunk=Buffer.concat([json,Buffer.alloc(jsonPadding,0x20)]),binary=Buffer.concat(chunks);
const header=Buffer.alloc(12),jh=Buffer.alloc(8),bh=Buffer.alloc(8);header.writeUInt32LE(0x46546c67);header.writeUInt32LE(2,4);header.writeUInt32LE(28+jsonChunk.length+binary.length,8);jh.writeUInt32LE(jsonChunk.length);jh.writeUInt32LE(0x4e4f534a,4);bh.writeUInt32LE(binary.length);bh.writeUInt32LE(0x004e4942,4);
const output=path.join(root,'public/models');fs.mkdirSync(output,{recursive:true});
fs.writeFileSync(path.join(output,'sztu-author.glb'),Buffer.concat([header,jh,jsonChunk,bh,binary]));
const manifest={id:'sztu-author-2024',source:'https://my.spline.design/untitled-aa64f4618ea03774ddb42bdc644ea535/',sourceSha256:crypto.createHash('sha256').update(source).digest('hex'),meshes:gltf.meshes.length,triangles,points,bounds:worldBox,geometry:'Original author Draco streams and transforms, no simplification',appearance:'Original author color layers; source texture image data absent',units:'author model units; requires explicit scene registration'};
fs.writeFileSync(path.join(output,'sztu-author.manifest.json'),JSON.stringify(manifest,null,2));
const decoderDir=path.join(output,'draco');fs.mkdirSync(decoderDir,{recursive:true});for(const name of ['draco_wasm_wrapper.js','draco_decoder.wasm','draco_decoder.js'])fs.copyFileSync(path.join(root,'node_modules/three/examples/jsm/libs/draco/gltf',name),path.join(decoderDir,name));fs.copyFileSync(path.join(root,'node_modules/three/examples/jsm/libs/draco/README.md'),path.join(decoderDir,'README.md'));
decoderModule.destroy(decoder);console.log(JSON.stringify(manifest,null,2));
