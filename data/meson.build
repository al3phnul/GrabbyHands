# SPDX-License-Identifier: GPL-3.0-or-later
datadir = get_option('datadir')

desktop_file = i18n.merge_file(
  input: app_id + '.desktop.in',
  output: app_id + '.desktop',
  type: 'desktop',
  po_dir: '../po',
  install: true,
  install_dir: datadir / 'applications',
)

desktop_validate = find_program('desktop-file-validate', required: false)
if desktop_validate.found()
  test('validate-desktop', desktop_validate, args: [desktop_file])
endif

metainfo_file = i18n.merge_file(
  input: app_id + '.metainfo.xml.in',
  output: app_id + '.metainfo.xml',
  po_dir: '../po',
  install: true,
  install_dir: datadir / 'metainfo',
)

appstreamcli = find_program('appstreamcli', required: false)
if appstreamcli.found()
  test('validate-metainfo', appstreamcli, args: ['validate', '--no-net', '--explain', metainfo_file])
endif

install_data(app_id + '.gschema.xml', install_dir: datadir / 'glib-2.0' / 'schemas')

compile_schemas = find_program('glib-compile-schemas', required: false)
if compile_schemas.found()
  test('validate-schema', compile_schemas, args: ['--strict', '--dry-run', meson.current_source_dir()])
endif

configure_file(
  input: app_id + '.service.in',
  output: app_id + '.service',
  configuration: conf,
  install: true,
  install_dir: datadir / 'dbus-1' / 'services',
)

install_data(
  'icons/hicolor/scalable/apps/' + app_id + '.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'scalable' / 'apps',
)
install_data(
  'icons/hicolor/symbolic/apps/' + app_id + '-symbolic.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'symbolic' / 'apps',
)

# GNOME Shell extension that tells GrabbyHands where windows are (native window capture)
# and, if the user asks, makes the system menu's screenshot button start GrabbyHands.
# Flatpak builds turn this off: a sandboxed app can't install into the host shell, so the
# extension ships as a separate zip on the Releases page instead.
if get_option('shell_extension')
  extension_dir = datadir / 'gnome-shell' / 'extensions' / 'grabbyhands@al3phnul.github.io'
  extension_schema = 'shell-extension/schemas/org.gnome.shell.extensions.grabbyhands.gschema.xml'
  install_data(
    'shell-extension/metadata.json',
    'shell-extension/extension.js',
    install_dir: extension_dir,
  )
  # The extension reads its one setting from its own folder, so the compiled schema goes
  # there too. (`gnome-extensions install` compiles it by itself for the zip.)
  install_data(extension_schema, install_dir: extension_dir / 'schemas')
  custom_target(
    'shell-extension-schemas',
    input: extension_schema,
    output: 'gschemas.compiled',
    command: [
      find_program('glib-compile-schemas'),
      '--strict',
      '--targetdir=@OUTDIR@',
      meson.current_source_dir() / 'shell-extension' / 'schemas',
    ],
    install: true,
    install_dir: extension_dir / 'schemas',
  )
endif
