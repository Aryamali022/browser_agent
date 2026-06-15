// Chrome cannot show the microphone permission prompt inside a side panel,
// so this page (a normal extension tab) obtains the grant once; it then
// applies to the whole extension, including the side panel's voice input.
const status = document.getElementById('status');

navigator.mediaDevices.getUserMedia({ audio: true })
  .then((stream) => {
    stream.getTracks().forEach((t) => t.stop()); // we only needed the grant
    document.body.classList.add('ok');
    status.textContent =
      'Microphone access granted ✓ — this tab will close. ' +
      'Use the mic button in the side panel.';
    setTimeout(() => window.close(), 2000);
  })
  .catch((err) => {
    document.body.classList.add('err');
    status.textContent =
      `Microphone access was blocked (${err.name}). ` +
      'Click the mic/camera icon in the address bar and choose "Allow", ' +
      'or enable it under chrome://settings/content/microphone — then reload this page.';
  });
