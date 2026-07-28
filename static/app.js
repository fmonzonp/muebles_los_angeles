'use strict';

const menuButton = document.querySelector('#menuBtn');
const nav = document.querySelector('#mainNav');
if (menuButton && nav) {
  menuButton.addEventListener('click', () => nav.classList.toggle('open'));
}

document.querySelectorAll('[data-confirm]').forEach((element) => {
  element.addEventListener('click', (event) => {
    if (!window.confirm(element.dataset.confirm)) event.preventDefault();
  });
});

const startInput = document.querySelector('#start_date');
const installmentInput = document.querySelector('#installments');
const duePreview = document.querySelector('#due_preview');
function updateDuePreview() {
  if (!startInput || !installmentInput || !duePreview || !startInput.value) return;
  const date = new Date(`${startInput.value}T12:00:00`);
  date.setDate(date.getDate() + (Number(installmentInput.value || 0) * 15));
  duePreview.value = date.toISOString().slice(0, 10);
}
if (startInput && installmentInput) {
  startInput.addEventListener('change', updateDuePreview);
  installmentInput.addEventListener('input', updateDuePreview);
  updateDuePreview();
}

const flashMessages = document.querySelectorAll('.flash');
flashMessages.forEach((message) => {
  window.setTimeout(() => message.classList.add('fade'), 5000);
});
