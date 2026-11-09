const [major, minor] = process.versions.node.match(/[0-9]+/g).map(Number);
if (!((major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major > 22)) {
  console.error('Node.js 22.12+ or 24 LTS is required.');
  process.exit(1);
}
console.log('Node.js ' + process.versions.node);
