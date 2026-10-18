const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('yuntongCameraBridge', {
  getStatus() {
    return ipcRenderer.invoke('camera:get-status');
  },
  getFrame(cameraId, afterSequence) {
    return ipcRenderer.invoke('camera:get-frame', { cameraId, afterSequence });
  },
  startDetection() {
    return ipcRenderer.invoke('camera:start-detection');
  },
  stopDetection() {
    return ipcRenderer.invoke('camera:stop-detection');
  },
  toggleRecording() {
    return ipcRenderer.invoke('camera:toggle-recording');
  },
  saveSnapshots() {
    return ipcRenderer.invoke('camera:save-snapshots');
  },
});
