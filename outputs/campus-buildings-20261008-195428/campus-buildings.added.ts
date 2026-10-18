type Point3 = { x: number; y: number; z: number };
type ProjectedPoint = { x: number; y: number; depth: number };
type ProjectPoint = (point: Point3) => ProjectedPoint;
type Block = { x: number; z: number; width: number; depth: number; bottom: number; top: number };
type CampusBuilding = {
  code: string;
  label: string[];
  x: number;
  z: number;
  heightMeters: number;
  blocks: Block[];
};

// Campus massing only: one scene unit represents 50 model metres, with the
// same scale on X/Y/Z. East is +X and north is -Z. Footprints and distances
// approximate the official campus map; unpublished heights are estimates.
// This scale never changes camera triangulation or detection coordinates.
const MODEL_METERS_PER_UNIT = 50;
function block(x: number, z: number, width: number, depth: number, topMeters: number, bottomMeters = 0): Block {
  return { x, z, width, depth, bottom: bottomMeters / MODEL_METERS_PER_UNIT, top: topMeters / MODEL_METERS_PER_UNIT };
}

function courtyard(width: number, depth: number, heightMeters: number): Block[] {
  const edge = 0.32;
  return [
    block(0, -(depth - edge) / 2, width, edge, heightMeters),
    block(0, (depth - edge) / 2, width, edge, heightMeters),
    block(-(width - edge) / 2, 0, edge, depth - 2 * edge, heightMeters),
    block((width - edge) / 2, 0, edge, depth - 2 * edge, heightMeters),
  ];
}

export const campusBuildings: CampusBuilding[] = [
  {
    code: 'C5', label: ['C-5 教学楼'], x: -1.45, z: -4.8, heightMeters: 28.75,
    // Five-storey teaching building with six wings joined by a spine.
    blocks: [block(0, 0.73, 3.9, 0.34, 28.75), ...[-1.75, -1.05, -0.35, 0.35, 1.05, 1.75].map((x) => block(x, -0.05, 0.4, 1.56, 28.75))],
  },
  {
    code: 'C3', label: ['行政楼'], x: -1.25, z: -2.2, heightMeters: 68,
    blocks: [block(0, 0, 2.05, 1.35, 16), block(-0.3, -0.16, 0.76, 0.92, 68, 16)],
  },
  {
    code: 'D3', label: ['中德智能制造学院'], x: 4.1, z: -2.25, heightMeters: 76,
    // Officially a four-storey podium and an eighteen-storey tower.
    blocks: [block(0, 0, 2.12, 1.68, 18), block(0.2, -0.14, 0.62, 1.16, 76, 18)],
  },
  {
    code: 'C2', label: ['图书馆'], x: -1.25, z: 0.4, heightMeters: 36,
    // Broad overhanging roof echoes the library's graduation-cap silhouette.
    blocks: [block(0, 0, 2.05, 1.36, 32), block(0, 0, 2.76, 1.85, 36, 32)],
  },
  {
    code: 'C1', label: ['人工智能学院'], x: 1.55, z: 0.4, heightMeters: 52,
    blocks: [...courtyard(2.35, 1.72, 18), block(-0.68, -0.25, 0.68, 1.04, 52, 18)],
  },
  {
    code: 'D1', label: ['城市交通与', '物流学院'], x: 4.8, z: 0.4, heightMeters: 48,
    // The official photo shows a courtyard podium and a higher eastern tower.
    blocks: [...courtyard(2.36, 1.76, 23), block(0.76, -0.21, 0.6, 1.08, 48, 23)],
  },
];

export function drawCampusBuildings(context: CanvasRenderingContext2D, project: ProjectPoint): void {
  const faces: { points: ProjectedPoint[]; depth: number; color: string }[] = [];
  const surfaces = [
    { corners: [0, 1, 2, 3], normal: { x: 0, y: -1, z: 0 }, color: '#e9b786' },
    { corners: [4, 7, 6, 5], normal: { x: 0, y: 1, z: 0 }, color: '#ffe1bc' },
    { corners: [0, 4, 5, 1], normal: { x: 0, y: 0, z: -1 }, color: '#f2c493' },
    { corners: [3, 2, 6, 7], normal: { x: 0, y: 0, z: 1 }, color: '#f8cfa2' },
    { corners: [0, 3, 7, 4], normal: { x: -1, y: 0, z: 0 }, color: '#ebbd8e' },
    { corners: [1, 5, 6, 2], normal: { x: 1, y: 0, z: 0 }, color: '#f6cda1' },
  ];
  for (const building of campusBuildings) for (const volume of building.blocks) {
    const x0 = building.x + volume.x - volume.width / 2;
    const x1 = x0 + volume.width;
    const z0 = building.z + volume.z - volume.depth / 2;
    const z1 = z0 + volume.depth;
    const vertices: Point3[] = [
      { x: x0, y: volume.bottom, z: z0 }, { x: x1, y: volume.bottom, z: z0 },
      { x: x1, y: volume.bottom, z: z1 }, { x: x0, y: volume.bottom, z: z1 },
      { x: x0, y: volume.top, z: z0 }, { x: x1, y: volume.top, z: z0 },
      { x: x1, y: volume.top, z: z1 }, { x: x0, y: volume.top, z: z1 },
    ];
    const projected = vertices.map(project);
    for (const surface of surfaces) {
      // Positive projected depth faces the viewer. Cull back faces and paint
      // all buildings from far to near, including after rotation and panning.
      const normalDepth = project(surface.normal).depth - project({ x: 0, y: 0, z: 0 }).depth;
      if (normalDepth <= 1e-8) continue;
      const points = surface.corners.map((index) => projected[index]);
      faces.push({ points, depth: points.reduce((sum, point) => sum + point.depth, 0) / points.length, color: surface.color });
    }
  }
  faces.sort((a, b) => a.depth - b.depth);
  context.save();
  context.lineWidth = 0.8;
  context.strokeStyle = '#bf9367';
  for (const face of faces) {
    context.beginPath();
    face.points.forEach((point, index) => { if (index === 0) context.moveTo(point.x, point.y); else context.lineTo(point.x, point.y); });
    context.closePath();
    context.fillStyle = face.color;
    context.fill();
    context.stroke();
  }
  context.font = '600 10px Inter, "Microsoft YaHei", sans-serif';
  context.textAlign = 'center';
  context.textBaseline = 'middle';
  for (const building of campusBuildings) {
    const anchor = project({ x: building.x, y: building.heightMeters / MODEL_METERS_PER_UNIT, z: building.z });
    const lines = building.label;
    const labelWidth = Math.max(...lines.map((text) => context.measureText(text).width)) + 12;
    const labelHeight = lines.length * 14 + 6;
    const labelY = anchor.y - labelHeight - 6;
    context.fillStyle = 'rgba(255, 249, 240, 0.94)';
    context.fillRect(anchor.x - labelWidth / 2, labelY, labelWidth, labelHeight);
    context.fillStyle = '#775132';
    lines.forEach((text, index) => context.fillText(text, anchor.x, labelY + 10 + index * 14));
  }
  context.restore();
}
