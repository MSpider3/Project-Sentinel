/* LoginView.vala - Secure startup login screen */

using Gtk;
using GLib;

namespace Sentinel {

    public class LoginView : Gtk.Box {
        private BackendService backend;
        private Gtk.PasswordEntry password_entry;
        private Gtk.Button login_button;
        private Gtk.Label error_label;
        private Gtk.Spinner spinner;
        public signal void authenticated ();

        public LoginView (BackendService backend) {
            GLib.Object (orientation: Gtk.Orientation.VERTICAL, spacing: 20);
            this.backend = backend;
            
            this.vexpand = true;
            this.hexpand = true;
            this.valign = Gtk.Align.CENTER;
            this.halign = Gtk.Align.CENTER;

            setup_ui ();
        }

        private void setup_ui () {
            var card = new Gtk.Box (Gtk.Orientation.VERTICAL, 24);
            card.add_css_class ("login-card");
            card.set_size_request (400, -1);
            append (card);

            var icon = new Gtk.Image.from_icon_name ("security-high-symbolic");
            icon.set_pixel_size (64);
            icon.set_margin_bottom (10);
            card.append (icon);

            var title = new Gtk.Label ("<b>Project Sentinel</b>");
            title.use_markup = true;
            title.add_css_class ("title-1");
            card.append (title);

            var info = new Gtk.Label ("Startup Authentication Required");
            info.add_css_class ("dim-label");
            card.append (info);

            var entry_box = new Gtk.Box (Gtk.Orientation.VERTICAL, 8);
            
            password_entry = new Gtk.PasswordEntry ();
            password_entry.placeholder_text = "System Password";
            password_entry.set_hexpand (true);
            password_entry.activate.connect (on_login_clicked);
            entry_box.append (password_entry);
            
            error_label = new Gtk.Label ("");
            error_label.add_css_class ("error-label");
            error_label.visible = false;
            entry_box.append (error_label);
            
            card.append (entry_box);

            var btn_box = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 12);
            btn_box.halign = Gtk.Align.CENTER;

            login_button = new Gtk.Button.with_label ("Login");
            login_button.add_css_class ("suggested-action");
            login_button.add_css_class ("pill-button");
            login_button.clicked.connect (on_login_clicked);
            btn_box.append (login_button);

            spinner = new Gtk.Spinner ();
            btn_box.append (spinner);

            card.append (btn_box);
        }

        private async void on_login_clicked () {
            var password = password_entry.get_text ();
            if (password == "") return;

            login_button.sensitive = false;
            password_entry.sensitive = false;
            spinner.start ();
            error_label.visible = false;

            var params = new Json.Object ();
            params.set_string_member ("password", password);

            var result = yield backend.call_method ("authenticate_startup_password", params);

            spinner.stop ();
            login_button.sensitive = true;
            password_entry.sensitive = true;

            if (result == null) {
                show_error ("Backend communication error");
                return;
            }

            var result_obj = result.get_object ();
            if (result_obj.get_boolean_member ("success")) {
                authenticated ();
            } else {
                show_error ("Invalid credentials or access denied");
                password_entry.set_text ("");
            }
        }

        private void show_error (string message) {
            error_label.label = message;
            error_label.visible = true;
        }
    }
}
