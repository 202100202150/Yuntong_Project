<template>
  <section ref="container" class="campus-scene" data-testid="campus-scene" :data-ready="loaded" :data-mode="sceneMode" :data-registered="registered" :data-model-loaded="loaded" :data-marker-count="labels.length" :data-trail-points="trailPointCount">
    <canvas ref="canvas" tabindex="0" aria-label="深圳技术大学三维校园模型，拖动旋转，右键拖动平移，滚轮缩放" @keydown="onKeydown" />
    <div class="scene-labels">
      <svg class="label-leaders"><g v-for="label in labels" v-show="label.visible" :key="label.key"><line :x1="label.anchorX" :y1="label.anchorY" :x2="label.x" :y2="label.y-5"/><circle :cx="label.anchorX" :cy="label.anchorY" r="3"/></g></svg>
      <button v-for="label in labels" v-show="label.visible" :key="label.key" class="scene-pin" :class="[label.kind,{selected:label.selected,held:label.held}]" :style="{left:label.x+'px',top:label.y+'px'}" :aria-label="label.title" @pointerdown.stop @click="selectLabel(label)">
        <span class="pin-title"><Video v-if="label.kind==='camera'" :size="15"/><Navigation v-else :size="15"/>{{label.text}}</span><i/>
      </button>
    </div>
    <div v-if="loading" class="scene-loading" role="status"><LoaderCircle class="spin" :size="22"/><span>正在载入校园模型…</span></div>
    <div v-else-if="loadError" class="scene-failure" role="alert"><span>{{loadError}}</span><button class="btn" @click="loadModel">重新加载模型</button></div>
    <button v-else class="scene-status" @click="emit('configure')"><MapPin :size="14"/>{{registered?'场景坐标已配准':'模型已加载 · 坐标未配准'}}</button>
    <div v-if="loaded" class="scene-zoom"><button :disabled="zoomInDisabled" aria-label="放大三维模型" title="放大" @click="zoomBy(1.2)"><Plus :size="20"/></button><button :disabled="zoomOutDisabled" aria-label="缩小三维模型" title="缩小" @click="zoomBy(1/1.2)"><Minus :size="20"/></button></div>
    <div class="scene-axis" aria-label="模型坐标轴，Y 向上"><svg viewBox="0 0 76 76"><line v-for="axis in axes" :key="axis.name" x1="38" y1="43" :x2="axis.x" :y2="axis.y" :stroke="axis.color" stroke-width="2"/><text v-for="axis in axes" :key="axis.name" :x="axis.x" :y="axis.y-4" :fill="axis.color" text-anchor="middle">{{axis.name}}</text></svg></div>
    <div v-if="loaded&&registered" class="scene-layers"><label><input v-model="showFrusta" type="checkbox" :disabled="!hasDirections"/>相机视域</label><label><input type="checkbox" :checked="showTrails" @change="setTrailsVisible(($event.target as HTMLInputElement).checked)"/>航迹</label><span v-if="following">正在跟随目标</span></div>
    <div v-if="selectedTarget" class="target-summary"><strong>{{selectedTarget.label||selectedTarget.id}}</strong><span>{{selectedTarget.trackingState==='held'?'短时保持':'定位中'}}</span><span>相对高度 {{selectedTarget.y.toFixed(1)}} m</span><button :aria-pressed="following===selectedTarget.id" @click="following===selectedTarget.id?following=null:focusTarget(selectedTarget.id)">{{following===selectedTarget.id?'停止跟随':'跟随目标'}}</button><button @click="focusVideos">查看关联视频 <ExternalLink :size="14"/></button></div>
  </section>
</template>

<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue';
import {Video,Navigation,LoaderCircle,MapPin,Plus,Minus,ExternalLink} from 'lucide-vue-next';
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {DRACOLoader} from 'three/addons/loaders/DRACOLoader.js';
import type {SpatialStatus,SpatialTarget,SpatialPoint} from '@/types/camera-types';
import type {SceneConfig,MonitorDevice,CameraDirection} from '@/types/monitor';
import {finitePoint,metricToCampus,validRegistration,appendObservation,type CampusObservation} from '@/lib/campus-registration';

const props=defineProps<{spatial:SpatialStatus;detecting:boolean;selectedCameraId?:string|null;selectedTargetId?:string|null;sceneConfig:SceneConfig;devices:MonitorDevice[]}>();
const emit=defineEmits<{(event:'select-camera',id:string):void;(event:'select-target',id:string):void;(event:'focus-videos',ids:string[]):void;(event:'ready',value:{meshes:number;triangles:number}):void;(event:'error',message:string):void;(event:'configure'):void}>();
const container=ref<HTMLElement|null>(null),canvas=ref<HTMLCanvasElement|null>(null);
const sceneMode=ref<'2d'|'3d'>('3d'),orbiting=ref(false),following=ref<string|null>(null),loaded=ref(false),loading=ref(false),loadError=ref('');
const showTrails=ref(true),showFrusta=ref(true),zoomInDisabled=ref(false),zoomOutDisabled=ref(false),trailPointCount=ref(0);
const registered=computed(()=>validRegistration(props.sceneConfig));
const targets=computed(()=>registered.value&&props.detecting&&props.spatial.state==='ready'?props.spatial.targets.filter(finitePoint):[]);
const selectedTarget=computed(()=>targets.value.find(target=>target.id===props.selectedTargetId)||null);
const canFollow=computed(()=>!!selectedTarget.value);
const hasDirections=computed(()=>Object.values(props.sceneConfig.cameraDirections||{}).some(validDirection));
type Label={key:string;id:string;kind:'camera'|'target';text:string;title:string;point:THREE.Vector3;visible:boolean;x:number;y:number;anchorX:number;anchorY:number;selected:boolean;held:boolean};
const labels=ref<Label[]>([]),axes=ref<Array<{name:string;color:string;x:number;y:number}>>([]);
let renderer:THREE.WebGLRenderer|null=null,scene:THREE.Scene,camera:THREE.PerspectiveCamera|THREE.OrthographicCamera,controls:OrbitControls,model:THREE.Object3D|null=null,overlays:THREE.Group;
let decoder:DRACOLoader|null=null,abort:AbortController|null=null,observer:ResizeObserver|null=null,frame=0,disposed=false,needsDraw=true,lastExpiry=0;
// Match the published author bird's-eye view; both modes use this same local model.
const DEFAULT_DIRECTION=new THREE.Vector3(-1.45,1,.5).normalize();
let span=1000,baseDistance=1800,aspect=1,orthoHeight=1000,defaultTarget=new THREE.Vector3(),lastDirection=DEFAULT_DIRECTION.clone(),modelBounds=new THREE.Box3(),viewEdited=false;
let projectedWidth=1000,projectedHeight=1000,planWidth=1000,planHeight=1000;
const history=new Map<string,CampusObservation[]>();
const world=(point:SpatialPoint)=>{const p=metricToCampus(point,props.sceneConfig);return new THREE.Vector3(p.x,p.y,p.z);};
function assetUrl(relative:string){return new URL(relative,new URL(import.meta.env.BASE_URL,document.baseURI)).href;}
function validDirection(direction:CameraDirection|undefined):direction is CameraDirection{return !!direction&&[direction.yawDeg,direction.pitchDeg,direction.horizontalFovDeg,direction.verticalFovDeg,direction.rangeMeters].every(Number.isFinite)&&direction.horizontalFovDeg>0&&direction.horizontalFovDeg<179&&direction.verticalFovDeg>0&&direction.verticalFovDeg<179&&direction.rangeMeters>0;}
function disposeObject(object:THREE.Object3D){object.traverse(child=>{const mesh=child as THREE.Mesh;if(mesh.geometry)mesh.geometry.dispose();if(mesh.material)for(const material of Array.isArray(mesh.material)?mesh.material:[mesh.material])material.dispose();});}
function requestDraw(){needsDraw=true;}
function makeControls(target:THREE.Vector3){controls?.dispose();controls=new OrbitControls(camera,canvas.value!);controls.target.copy(target);controls.enableDamping=true;controls.dampingFactor=.15;controls.enableRotate=sceneMode.value==='3d';controls.minDistance=span*.08;controls.maxDistance=span*5;controls.minZoom=.35;controls.maxZoom=12;controls.maxPolarAngle=sceneMode.value==='2d'?Math.PI:Math.PI*.48;controls.autoRotateSpeed=.35;controls.autoRotate=orbiting.value&&sceneMode.value==='3d';controls.addEventListener('change',requestDraw);controls.addEventListener('start',()=>{following.value=null;viewEdited=true;});controls.update();}
function measureModelProjection(){if(!model)return;const right=new THREE.Vector3().crossVectors(new THREE.Vector3(0,1,0),DEFAULT_DIRECTION).normalize(),up=new THREE.Vector3().crossVectors(DEFAULT_DIRECTION,right).normalize(),point=new THREE.Vector3();let minH=Infinity,maxH=-Infinity,minV=Infinity,maxV=-Infinity,minDepth=Infinity,maxDepth=-Infinity;model.updateMatrixWorld(true);model.traverse(child=>{const mesh=child as THREE.Mesh;if(!mesh.isMesh)return;const position=mesh.geometry.attributes.position;for(let i=0;i<position.count;i++){point.fromBufferAttribute(position,i).applyMatrix4(mesh.matrixWorld);const h=point.dot(right),v=point.dot(up),depth=point.dot(DEFAULT_DIRECTION);minH=Math.min(minH,h);maxH=Math.max(maxH,h);minV=Math.min(minV,v);maxV=Math.max(maxV,v);minDepth=Math.min(minDepth,depth);maxDepth=Math.max(maxDepth,depth);}});projectedWidth=maxH-minH;projectedHeight=maxV-minV;defaultTarget=right.multiplyScalar((minH+maxH)/2).addScaledVector(up,(minV+maxV)/2).addScaledVector(DEFAULT_DIRECTION,(minDepth+maxDepth)/2);}
function planUp(){return new THREE.Vector3(-lastDirection.x,0,-lastDirection.z).normalize();}
function measurePlanProjection(){
 if(!model)return;
 const right=new THREE.Vector3().crossVectors(planUp(),new THREE.Vector3(0,1,0)),up=planUp(),point=new THREE.Vector3();
 let minH=Infinity,maxH=-Infinity,minV=Infinity,maxV=-Infinity;
 model.traverse(child=>{const mesh=child as THREE.Mesh;if(!mesh.isMesh)return;const positions=mesh.geometry.attributes.position;for(let i=0;i<positions.count;i++){point.fromBufferAttribute(positions,i).applyMatrix4(mesh.matrixWorld);const h=point.dot(right),v=point.dot(up);minH=Math.min(minH,h);maxH=Math.max(maxH,h);minV=Math.min(minV,v);maxV=Math.max(maxV,v);}});
 planWidth=maxH-minH;planHeight=maxV-minV;
}
function fitDimensions(){if(modelBounds.isEmpty())return;baseDistance=span*2;orthoHeight=(sceneMode.value==='2d'?Math.max(planHeight,planWidth/aspect):Math.max(projectedHeight,projectedWidth/aspect))*1.09;}
function resize(){if(!renderer||!container.value)return;const width=container.value.clientWidth,height=container.value.clientHeight;if(!width||!height)return;aspect=width/height;renderer.setSize(width,height,false);fitDimensions();if(camera instanceof THREE.PerspectiveCamera)camera.aspect=aspect;else{camera.left=-orthoHeight*aspect/2;camera.right=orthoHeight*aspect/2;camera.top=orthoHeight/2;camera.bottom=-orthoHeight/2;}if(loaded.value&&!viewEdited&&!following.value&&!orbiting.value){camera.position.copy(defaultTarget).addScaledVector(sceneMode.value==='2d'?new THREE.Vector3(0,1,0):DEFAULT_DIRECTION,sceneMode.value==='2d'?span*2:baseDistance);controls.target.copy(defaultTarget);camera.lookAt(defaultTarget);}camera.updateProjectionMatrix();requestDraw();}
async function loadModel(){if(loading.value||disposed||!renderer)return;loading.value=true;loadError.value='';abort?.abort();abort=new AbortController();try{
 const response=await fetch(assetUrl('models/sztu-author.glb'),{signal:abort.signal});if(!response.ok)throw Error('校园模型读取失败（'+response.status+'）');const bytes=await response.arrayBuffer();
 decoder?.dispose();decoder=new DRACOLoader();decoder.setDecoderPath(assetUrl('models/draco/'));decoder.setWorkerLimit(4);const loader=new GLTFLoader();loader.setDRACOLoader(decoder);const gltf=await loader.parseAsync(bytes,assetUrl('models/'));
 if(disposed){disposeObject(gltf.scene);return;}if(model){scene.remove(model);disposeObject(model);}model=gltf.scene;scene.add(model);
 const box=new THREE.Box3().setFromObject(model),size=box.getSize(new THREE.Vector3());modelBounds.copy(box);span=Math.max(size.x,size.z,size.y);measureModelProjection();fitDimensions();
 let meshes=0,triangles=0;model.traverse(child=>{if((child as THREE.Mesh).isMesh){const mesh=child as THREE.Mesh;meshes++;triangles+=(mesh.geometry.index?.count||mesh.geometry.attributes.position.count)/3;}});
 loaded.value=true;resetView();rebuildOverlays();emit('ready',{meshes,triangles});
 }catch(error){if(disposed||(error as Error).name==='AbortError')return;loadError.value=(error as Error).message||'校园模型加载失败';emit('error',loadError.value);}finally{loading.value=false;}}
function switchMode(mode:'2d'|'3d'){if(!renderer||mode===sceneMode.value)return;const target=controls.target.clone(),distance=camera.position.distanceTo(target),zoom=camera instanceof THREE.OrthographicCamera?camera.zoom:baseDistance/distance;if(sceneMode.value==='3d')lastDirection=camera.position.clone().sub(target).normalize();sceneMode.value=mode;orbiting.value=false;if(mode==='2d')measurePlanProjection();fitDimensions();
 if(mode==='2d'){camera=new THREE.OrthographicCamera(-orthoHeight*aspect/2,orthoHeight*aspect/2,orthoHeight/2,-orthoHeight/2,.1,span*20);camera.position.copy(target).add(new THREE.Vector3(0,span*2,0));camera.up.copy(planUp());camera.zoom=Math.max(.35,Math.min(12,zoom));}
 else{camera=new THREE.OrthographicCamera(-orthoHeight*aspect/2,orthoHeight*aspect/2,orthoHeight/2,-orthoHeight/2,.1,span*12);camera.position.copy(target).addScaledVector(lastDirection,baseDistance);camera.zoom=Math.max(.35,Math.min(12,zoom));camera.up.set(0,1,0);}
 camera.lookAt(target);makeControls(target);resize();}
function resetView(){
 if(!controls||!loaded.value)return false;
 viewEdited=false;lastDirection.copy(DEFAULT_DIRECTION);
 if(sceneMode.value==='2d')measurePlanProjection();
 fitDimensions();following.value=null;orbiting.value=false;camera.zoom=1;
 const target=defaultTarget.clone();
 if(sceneMode.value==='2d'){camera.position.copy(target).add(new THREE.Vector3(0,span*2,0));camera.up.copy(planUp());}
 else{camera.position.copy(target).addScaledVector(lastDirection,baseDistance);camera.up.set(0,1,0);}
 camera.lookAt(target);camera.near=camera instanceof THREE.PerspectiveCamera?span*.005:.1;camera.far=span*12;camera.updateProjectionMatrix();
 // A fresh controller clears pending drag/pan damping and auto-orbit deltas.
 // Moving the camera alone leaves those deltas active on the next frame.
 makeControls(target);resize();requestDraw();return true;
}
function zoomBy(factor:number){if(!controls||!Number.isFinite(factor)||factor<=0)return;viewEdited=true;if(camera instanceof THREE.OrthographicCamera){camera.zoom=THREE.MathUtils.clamp(camera.zoom*factor,.35,12);camera.updateProjectionMatrix();}else{const offset=camera.position.clone().sub(controls.target),distance=THREE.MathUtils.clamp(offset.length()/factor,span*.08,span*5);camera.position.copy(controls.target).add(offset.setLength(distance));}controls.update();requestDraw();}
function toggleOrbit(){if(!controls||sceneMode.value==='2d'||!loaded.value)return false;orbiting.value=!orbiting.value;controls.autoRotate=orbiting.value;following.value=null;requestDraw();return orbiting.value;}
function focusTarget(id?:string|null){const target=targets.value.find(target=>target.id===(id||props.selectedTargetId));if(!target){emit('error','当前没有可跟随的有效定位目标');return false;}following.value=target.id;orbiting.value=false;controls.autoRotate=false;follow();return true;}
function follow(){const target=targets.value.find(target=>target.id===following.value);if(!target){following.value=null;return;}const point=world(target),offset=camera.position.clone().sub(controls.target);controls.target.copy(point);camera.position.copy(point).add(offset);controls.update();requestDraw();}
function setTrailsVisible(value:boolean){showTrails.value=value;history.clear();rebuildOverlays();}
function line(points:THREE.Vector3[],color:number,opacity=1){return new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color,transparent:opacity<1,opacity,depthTest:false}));}
function frustum(point:SpatialPoint,direction:CameraDirection){const yaw=direction.yawDeg*Math.PI/180,pitch=direction.pitchDeg*Math.PI/180,forward=new THREE.Vector3(Math.sin(yaw)*Math.cos(pitch),Math.sin(pitch),Math.cos(yaw)*Math.cos(pitch)),left=new THREE.Vector3(Math.cos(yaw),0,-Math.sin(yaw)),up=new THREE.Vector3().crossVectors(forward,left).normalize(),range=direction.rangeMeters,center=new THREE.Vector3(point.x,point.y,point.z).addScaledVector(forward,range),w=range*Math.tan(direction.horizontalFovDeg*Math.PI/360),h=range*Math.tan(direction.verticalFovDeg*Math.PI/360),origin=world(point),corners=[[-1,-1],[1,-1],[1,1],[-1,1]].map(([x,y])=>world(center.clone().addScaledVector(left,x*w).addScaledVector(up,y*h)));
 const positions:number[]=[];for(let i=0;i<4;i++)positions.push(...origin.toArray(),...corners[i].toArray(),...corners[(i+1)%4].toArray());const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));overlays.add(new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({color:0x2fbbf7,transparent:true,opacity:.1,side:THREE.DoubleSide,depthWrite:false})));for(const corner of corners)overlays.add(line([origin,corner],0x159fdf,.38));overlays.add(line([...corners,corners[0]],0x159fdf,.38));}
function rebuildOverlays(){if(!overlays)return;for(const child of [...overlays.children]){overlays.remove(child);disposeObject(child);}labels.value=[];trailPointCount.value=0;if(!registered.value||!loaded.value){history.clear();following.value=null;requestDraw();return;}
 const next:Label[]=[];const readyCameras=!['uncalibrated','error'].includes(props.spatial.state)?props.spatial.cameras:{};
 for(const device of props.devices){const point=(readyCameras as Record<string,SpatialPoint>)[device.id]||props.sceneConfig.cameraPositions?.[device.id];if(!finitePoint(point))continue;const position=world(point),selected=props.selectedCameraId===device.id;const marker=new THREE.Mesh(new THREE.SphereGeometry(span*.0028,12,8),new THREE.MeshBasicMaterial({color:selected?0x005bff:0x297af2,depthTest:false}));marker.position.copy(position);marker.renderOrder=10;overlays.add(marker);next.push({key:'camera:'+device.id,id:device.id,kind:'camera',text:device.code||device.label,title:device.label,point:position,visible:true,x:0,y:0,anchorX:0,anchorY:0,selected,held:false});const direction=props.sceneConfig.cameraDirections?.[device.id];if(showFrusta.value&&validDirection(direction))frustum(point,direction);}
 for(const target of targets.value){const position=world(target),held=target.trackingState==='held';const marker=new THREE.Mesh(new THREE.OctahedronGeometry(span*.004),new THREE.MeshBasicMaterial({color:held?0x7f8b9c:0x006cff,depthTest:false}));marker.position.copy(position);marker.renderOrder=11;overlays.add(marker);next.push({key:'target:'+target.id,id:target.id,kind:'target',text:target.label||target.id,title:(target.label||target.id)+' · 相对高度 '+target.y.toFixed(1)+' m',point:position,visible:true,x:0,y:0,anchorX:0,anchorY:0,selected:props.selectedTargetId===target.id,held});
  if(showTrails.value){const points=(history.get(target.id)||[]).filter(item=>Date.now()-item.receivedAt<=60_000);history.set(target.id,points);trailPointCount.value+=points.length;let segment:THREE.Vector3[]=[];for(let i=0;i<points.length;i++){if(i&&points[i].receivedAt-points[i-1].receivedAt>4000){if(segment.length>1)overlays.add(line(segment,0x0877ff));segment=[];}segment.push(world(points[i]));}if(segment.length>1)overlays.add(line(segment,0x0877ff));}
 }
 labels.value=next;if(following.value)follow();requestDraw();}
function observeTargets(){if(!props.detecting||!registered.value||props.spatial.state!=='ready'){history.clear();following.value=null;}else{const active=new Set(targets.value.map(target=>target.id));for(const id of history.keys())if(!active.has(id))history.delete(id);if(showTrails.value){const now=Date.now();for(const target of targets.value)if(target.trackingState==='measured')history.set(target.id,appendObservation(history.get(target.id)||[],target,now));}}rebuildOverlays();}
function selectLabel(label:Label){if(label.kind==='camera')emit('select-camera',label.id);else emit('select-target',label.id);}
function focusVideos(){const ids=props.devices.filter(device=>device.isStereo).map(device=>device.id);if(ids.length)emit('focus-videos',ids);else emit('error','没有可关联的双目摄像机');}
function updateLabels(){
 if(!container.value)return;
 const width=container.value.clientWidth,height=container.value.clientHeight;camera.updateMatrixWorld();
 const occupied:Array<{x:number;y:number;width:number;height:number}>=[];
 for(const label of [...labels.value].sort((a,b)=>Number(b.selected)-Number(a.selected))){
  const p=label.point.clone().project(camera);label.visible=p.z>=-1&&p.z<=1&&Math.abs(p.x)<1.05&&Math.abs(p.y)<1.05;
  label.anchorX=(p.x*.5+.5)*width;label.anchorY=(-p.y*.5+.5)*height;
  if(!label.visible)continue;
  const boxWidth=Math.max(65,Math.min(220,label.text.length*13+34)),boxHeight=29;
  let chosen={x:label.anchorX,y:label.anchorY-12};
  for(const [dx,dy] of [[0,-12],[-65,-12],[65,-12],[0,-48],[-80,-48],[80,-48],[0,52],[-80,52],[80,52],[0,-84]]){
   const x=Math.max(boxWidth/2+6,Math.min(width-boxWidth/2-6,label.anchorX+dx)),y=Math.max(70,Math.min(height-125,label.anchorY+dy));
   const rect={x:x-boxWidth/2,y:y-boxHeight,width:boxWidth,height:boxHeight};
   if(!occupied.some(other=>rect.x<other.x+other.width+6&&rect.x+rect.width+6>other.x&&rect.y<other.y+other.height+5&&rect.y+rect.height+5>other.y)){chosen={x,y};break;}
  }
  label.x=chosen.x;label.y=chosen.y;occupied.push({x:chosen.x-boxWidth/2,y:chosen.y-boxHeight,width:boxWidth,height:boxHeight});
 }
 const inverse=camera.quaternion.clone().invert();
 axes.value=[{name:'X',color:'#ef5350',point:new THREE.Vector3(1,0,0)},{name:'Y',color:'#16a05a',point:new THREE.Vector3(0,1,0)},{name:'Z',color:'#1672f2',point:new THREE.Vector3(0,0,1)}].map(axis=>{const p=axis.point.applyQuaternion(inverse);return{name:axis.name,color:axis.color,x:38+p.x*23,y:43-p.y*23};});
 const zoom=camera instanceof THREE.OrthographicCamera?camera.zoom:baseDistance/camera.position.distanceTo(controls.target);
 zoomInDisabled.value=camera instanceof THREE.OrthographicCamera?zoom>=12-.001:camera.position.distanceTo(controls.target)<=span*.0801;
 zoomOutDisabled.value=camera instanceof THREE.OrthographicCamera?zoom<=.3501:camera.position.distanceTo(controls.target)>=span*4.999;
}

function tick(now:number){if(disposed)return;const changed=controls?.update();if(now-lastExpiry>1000){lastExpiry=now;if(showTrails.value&&history.size)rebuildOverlays();}if(renderer&&camera&&(needsDraw||changed||controls.autoRotate)){needsDraw=false;renderer.render(scene,camera);updateLabels();}frame=requestAnimationFrame(tick);}
function onKeydown(event:KeyboardEvent){if(event.ctrlKey||event.altKey||event.metaKey)return;if(['+','=','-','Home'].includes(event.key)){event.preventDefault();if(event.key==='Home')resetView();else zoomBy(event.key==='-'?1/1.2:1.2);}else if(event.key==='Escape'){following.value=null;orbiting.value=false;if(controls)controls.autoRotate=false;}}
// Preserve the source voxel geometry, including gaps between vegetation planes.
// Linear exposure lifts its original colors within the light product theme.
onMounted(()=>{try{renderer=new THREE.WebGLRenderer({canvas:canvas.value!,antialias:true,alpha:false});renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));renderer.setClearColor(0xe4ebf4);renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.LinearToneMapping;renderer.toneMappingExposure=1.45;scene=new THREE.Scene();scene.add(new THREE.AmbientLight(0xffffff,1.7));const sun=new THREE.DirectionalLight(0xffffff,1.1);sun.position.set(-400,1000,300);scene.add(sun);overlays=new THREE.Group();scene.add(overlays);camera=new THREE.OrthographicCamera(-500,500,500,-500,.1,20000);camera.position.set(1000,1300,1000);makeControls(defaultTarget);observer=new ResizeObserver(resize);observer.observe(container.value!);resize();frame=requestAnimationFrame(tick);void loadModel();}catch(error){loadError.value='无法初始化三维渲染：'+(error as Error).message;emit('error',loadError.value);}});
onUnmounted(()=>{disposed=true;abort?.abort();observer?.disconnect();cancelAnimationFrame(frame);controls?.dispose();decoder?.dispose();if(scene)disposeObject(scene);renderer?.dispose();});
watch(()=>[props.spatial,props.detecting],observeTargets,{deep:true});
watch(()=>props.sceneConfig,()=>{history.clear();rebuildOverlays();},{deep:true});
watch(()=>[props.devices,props.selectedCameraId,props.selectedTargetId,showFrusta.value],rebuildOverlays,{deep:true});
defineExpose({switchMode,resetView,zoomBy,toggleOrbit,focusTarget,setTrailsVisible,sceneMode,orbiting,following,canFollow,zoomInDisabled,zoomOutDisabled,getViewState:()=>({position:camera.position.toArray(),target:controls.target.toArray(),mode:sceneMode.value,zoom:camera.zoom,defaultDistance:baseDistance})});
</script>

<style scoped>
.campus-scene{position:relative;width:100%;height:100%;min-height:260px;overflow:hidden;background:#e4ebf4;border-radius:9px;isolation:isolate}.campus-scene>canvas{width:100%;height:100%;display:block;touch-action:none;outline:none}.campus-scene>canvas:focus-visible{outline:2px solid #1671ff;outline-offset:-3px}.scene-labels{position:absolute;inset:0;pointer-events:none}.scene-pin{position:absolute;pointer-events:auto;border:0;background:none;padding:0;transform:translate(-50%,-100%);display:grid;justify-items:center;gap:3px;cursor:pointer;filter:drop-shadow(0 2px 3px #23385a30)}.pin-title{display:flex;gap:5px;align-items:center;white-space:nowrap;border-radius:5px;padding:4px 7px;border:1px solid #6c91c3;background:#fff;color:#172844;font-size:13px;font-weight:650}.label-leaders{position:absolute;inset:0;width:100%;height:100%;overflow:visible;pointer-events:none}.label-leaders line{stroke:#347bea;stroke-width:1;opacity:.7}.label-leaders circle{fill:#0767ff;stroke:white;stroke-width:1.5}.scene-pin i{display:none;width:9px;height:9px;border:2px solid #fff;border-radius:50%;background:#1268ff}.scene-pin.selected .pin-title,.scene-pin.target .pin-title{background:#0767ff;color:#fff;border-color:#0767ff}.scene-pin.held .pin-title{background:#6e7c8f;border-color:#6e7c8f}.scene-loading,.scene-failure{position:absolute;inset:0;display:flex;gap:12px;align-items:center;justify-content:center;color:#4f6380;background:#e7edf4e6}.scene-failure{flex-direction:column;padding:24px;text-align:center}.scene-status{position:absolute;top:12px;left:12px;display:flex;align-items:center;gap:6px;border:1px solid #d6dfeb;border-radius:6px;background:#fffffff0;padding:7px 9px;color:#506580;font-size:12px;cursor:pointer}.scene-zoom{position:absolute;right:12px;bottom:125px;display:grid;box-shadow:0 2px 5px #21364e20;border:1px solid #d1dbea;border-radius:6px;overflow:hidden}.scene-zoom button{display:grid;place-items:center;width:35px;height:35px;border:0;background:white;color:#1a2e49;cursor:pointer}.scene-zoom button+button{border-top:1px solid #e3e9f2}.scene-zoom button:disabled{color:#b3bfce;cursor:default}.scene-axis{position:absolute;right:10px;bottom:38px;width:72px;height:72px;background:#ffffffec;border:1px solid #d4deeb;border-radius:9px;pointer-events:none}.scene-axis svg{width:100%;height:100%;font:600 12px 'Yuntong Sans',sans-serif}.scene-layers{position:absolute;bottom:10px;left:12px;display:flex;align-items:center;gap:14px;font-size:12px;color:#3e5470}.scene-layers label{display:flex;align-items:center;gap:4px;background:#ffffffc9;border-radius:4px;padding:3px}.scene-layers input{accent-color:#1268ff;width:14px;height:14px}.target-summary{position:absolute;left:12px;bottom:43px;right:95px;display:flex;align-items:center;flex-wrap:wrap;gap:10px;border:1px solid #d1dfef;border-radius:6px;background:#fffffff2;padding:8px 10px;font-size:12px;color:#354966;box-shadow:0 2px 7px #29415b14}.target-summary strong{color:#14243b}.target-summary button{display:inline-flex;align-items:center;gap:4px;border:0;border-radius:4px;background:#eaf2ff;color:#0767ff;font:inherit;cursor:pointer;padding:4px 6px}.spin{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.spin{animation:none}}
</style>
