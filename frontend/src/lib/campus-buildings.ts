type Point3 = { x: number; y: number; z: number };
type ProjectedPoint = { x: number; y: number; depth: number };
type ProjectPoint = (point: Point3) => ProjectedPoint;
type Block = { x: number; z: number; width: number; depth: number; bottom: number; top: number };
type CampusBuilding = {
  code: string;
  x: number;
  z: number;
  heightMeters: number;
  blocks: Block[];
};

// The base campus model uses 50 model metres per unit on X/Y/Z. East is +X
// and north is -Z. Footprints approximate the official map; heights include
// estimates. The final layout below is centered and enlarged uniformly.
// These model transforms never change camera or detection coordinates.
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

const campusLayout: CampusBuilding[] = [
  {
    code: 'C5', x: -1.45, z: -4.8, heightMeters: 28.75,
    // Five-storey teaching building with six wings joined by a spine.
    blocks: [block(0, 0.73, 3.9, 0.34, 28.75), ...[-1.75, -1.05, -0.35, 0.35, 1.05, 1.75].map((x) => block(x, -0.05, 0.4, 1.56, 28.75))],
  },
  {
    code: 'C3', x: -1.25, z: -2.2, heightMeters: 68,
    blocks: [block(0, 0, 2.05, 1.35, 16), block(-0.3, -0.16, 0.76, 0.92, 68, 16)],
  },
  {
    code: 'D3', x: 4.1, z: -2.25, heightMeters: 76,
    // Officially a four-storey podium and an eighteen-storey tower.
    blocks: [block(0, 0, 2.12, 1.68, 18), block(0.2, -0.14, 0.62, 1.16, 76, 18)],
  },
  {
    code: 'C2', x: -1.25, z: 0.4, heightMeters: 36,
    // Broad overhanging roof echoes the library's graduation-cap silhouette.
    blocks: [block(0, 0, 2.05, 1.36, 32), block(0, 0, 2.76, 1.85, 36, 32)],
  },
  {
    code: 'C1', x: 1.55, z: 0.4, heightMeters: 52,
    blocks: [...courtyard(2.35, 1.72, 18), block(-0.68, -0.25, 0.68, 1.04, 52, 18)],
  },
  {
    code: 'D1', x: 4.8, z: 0.4, heightMeters: 48,
    // The official photo shows a courtyard podium and a higher eastern tower.
    blocks: [...courtyard(2.36, 1.76, 23), block(0.76, -0.21, 0.6, 1.08, 48, 23)],
  },
];

// Center the complete footprint (including roof overhangs) on the origin.
// A 17% uniform enlargement leaves over half a grid unit at both X edges.
const footprints = campusLayout.flatMap((building) => building.blocks.map((volume) => ({
  minX: building.x + volume.x - volume.width / 2,
  maxX: building.x + volume.x + volume.width / 2,
  minZ: building.z + volume.z - volume.depth / 2,
  maxZ: building.z + volume.z + volume.depth / 2,
})));
const centerX = (Math.min(...footprints.map((footprint) => footprint.minX)) + Math.max(...footprints.map((footprint) => footprint.maxX))) / 2;
const centerZ = (Math.min(...footprints.map((footprint) => footprint.minZ)) + Math.max(...footprints.map((footprint) => footprint.maxZ))) / 2;
const CAMPUS_SCALE = 1.17;
export const campusBuildings: CampusBuilding[] = campusLayout.map((building) => ({
  ...building,
  x: (building.x - centerX) * CAMPUS_SCALE,
  z: (building.z - centerZ) * CAMPUS_SCALE,
  blocks: building.blocks.map((volume) => ({
    x: volume.x * CAMPUS_SCALE,
    z: volume.z * CAMPUS_SCALE,
    width: volume.width * CAMPUS_SCALE,
    depth: volume.depth * CAMPUS_SCALE,
    bottom: volume.bottom * CAMPUS_SCALE,
    top: volume.top * CAMPUS_SCALE,
  })),
}));

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
  context.restore();
}

type PlanCell = { x0: number; x1: number; z0: number; z1: number; top: number };
type PlanEdge = { horizontal: boolean; coordinate: number; start: number; end: number };
const PLAN_TOLERANCE = 1e-8;

function planCoordinates(values: number[]): number[] {
  return values.sort((a, b) => a - b).filter((value, index, sorted) => index === 0 || value - sorted[index - 1] > PLAN_TOLERANCE);
}

function createCampusPlan(building: CampusBuilding): { cells: PlanCell[]; edges: PlanEdge[] } {
  const footprints = building.blocks.map((volume) => ({
    x0: building.x + volume.x - volume.width / 2,
    x1: building.x + volume.x + volume.width / 2,
    z0: building.z + volume.z - volume.depth / 2,
    z1: building.z + volume.z + volume.depth / 2,
    top: volume.top,
  }));
  const xs = planCoordinates(footprints.flatMap((footprint) => [footprint.x0, footprint.x1]));
  const zs = planCoordinates(footprints.flatMap((footprint) => [footprint.z0, footprint.z1]));
  const heights = zs.slice(0, -1).map((z0, row) => xs.slice(0, -1).map((x0, column) => {
    const x = (x0 + xs[column + 1]) / 2;
    const z = (z0 + zs[row + 1]) / 2;
    const covering = footprints.filter((footprint) => x > footprint.x0 - PLAN_TOLERANCE && x < footprint.x1 + PLAN_TOLERANCE && z > footprint.z0 - PLAN_TOLERANCE && z < footprint.z1 + PLAN_TOLERANCE);
    return covering.length ? Math.max(...covering.map((footprint) => footprint.top)) : undefined;
  }));
  const cells: PlanCell[] = [];
  const edges: PlanEdge[] = [];
  for (let row = 0; row < heights.length; row += 1) for (let column = 0; column < heights[row].length; column += 1) {
    const top = heights[row][column];
    if (top === undefined) continue;
    const x0 = xs[column];
    const x1 = xs[column + 1];
    const z0 = zs[row];
    const z1 = zs[row + 1];
    cells.push({ x0, x1, z0, z1, top });
    const left = heights[row][column - 1];
    const above = heights[row - 1]?.[column];
    // Equal-height neighbours form one roof. A height change retains the
    // tower/podium outline; uncovered neighbours retain courtyard outlines.
    if (left === undefined || Math.abs(top - left) > PLAN_TOLERANCE) edges.push({ horizontal: false, coordinate: x0, start: z0, end: z1 });
    if (above === undefined || Math.abs(top - above) > PLAN_TOLERANCE) edges.push({ horizontal: true, coordinate: z0, start: x0, end: x1 });
    if (heights[row][column + 1] === undefined) edges.push({ horizontal: false, coordinate: x1, start: z0, end: z1 });
    if (heights[row + 1]?.[column] === undefined) edges.push({ horizontal: true, coordinate: z1, start: x0, end: x1 });
  }
  edges.sort((a, b) => Number(a.horizontal) - Number(b.horizontal) || a.coordinate - b.coordinate || a.start - b.start);
  const merged: PlanEdge[] = [];
  for (const edge of edges) {
    const previous = merged[merged.length - 1];
    if (previous && previous.horizontal === edge.horizontal && Math.abs(previous.coordinate - edge.coordinate) <= PLAN_TOLERANCE && edge.start <= previous.end + PLAN_TOLERANCE) previous.end = Math.max(previous.end, edge.end);
    else merged.push({ ...edge });
  }
  return { cells, edges: merged };
}

const campusPlans = campusBuildings.map(createCampusPlan);

// The plan projection uses X/Z only, looking from +Y towards -Y. Paint all
// roof cells in one fill so adjoining blocks cannot produce hairline gaps.
export function drawCampusBuildingsPlan(context: CanvasRenderingContext2D, project: ProjectPoint): void {
  context.save();
  context.fillStyle = '#ffe1bc';
  context.strokeStyle = '#bf9367';
  context.lineWidth = 0.8;
  for (const plan of campusPlans) {
    context.beginPath();
    for (const cell of plan.cells) {
      const points = [
        { x: cell.x0, y: cell.top, z: cell.z0 }, { x: cell.x1, y: cell.top, z: cell.z0 },
        { x: cell.x1, y: cell.top, z: cell.z1 }, { x: cell.x0, y: cell.top, z: cell.z1 },
      ].map(project);
      points.forEach((point, index) => { if (index === 0) context.moveTo(point.x, point.y); else context.lineTo(point.x, point.y); });
      context.closePath();
    }
    context.fill();
    context.beginPath();
    for (const edge of plan.edges) {
      const start = project({ x: edge.horizontal ? edge.start : edge.coordinate, y: 0, z: edge.horizontal ? edge.coordinate : edge.start });
      const end = project({ x: edge.horizontal ? edge.end : edge.coordinate, y: 0, z: edge.horizontal ? edge.coordinate : edge.end });
      context.moveTo(start.x, start.y);
      context.lineTo(end.x, end.y);
    }
    context.stroke();
  }
  context.restore();
}
