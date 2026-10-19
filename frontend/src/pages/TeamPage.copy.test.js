// Sentence-case guard for TeamPage labels (help-center 06 uses these labels).
const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, 'TeamPage.js'), 'utf8');

test.each([
  'Invite Member', 'Invite Team Member', 'Email Address', 'Send Invitation',
  'Team Members', 'Last Active', 'Make Admin', 'Make Manager', 'Make User',
  'Make Viewer', 'Pending Invitations',
])('TeamPage has no Title Case label "%s"', (label) => {
  expect(source).not.toContain(label);
});

test.each(['Invite member', 'Email address'])('TeamPage uses "%s"', (label) => {
  expect(source).toContain(label);
});
