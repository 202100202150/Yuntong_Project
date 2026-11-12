const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('yuntongCameraBridge', {
  getStatus() {
    return ipcRenderer.invoke('camera:get-status');
  },
  getFrame(cameraId, afterSequence) {
    return ipcRenderer.invoke('camera:get-frame', { cameraId, afterSequence });
  },
  getPreviewPair(afterSequences, epoch) {
    return ipcRenderer.invoke('camera:get-preview-pair', { afterSequences, epoch });
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
