import React from 'react';
import { createRoot } from 'react-dom/client';
import DualCameraWorkspace from '@/components/dual-camera-workspace';
import './desktop.css';

const root = document.getElementById('root');

if (!root) throw new Error('Desktop renderer root element is missing.');

createRoot(root).render(
  <React.StrictMode>
    <DualCameraWorkspace />
  </React.StrictMode>,
);
