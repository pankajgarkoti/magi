# MAGI installer kickstart: installs the MAGI image stored on this ISO (offline, no download).
# Anaconda asks for the language, disk and time; GNOME Initial Setup creates the user on the
# first boot. Afterwards the system tracks @IMAGE_REF@, so `sudo bootc upgrade` updates it.
ostreecontainer --url=/run/install/repo/magi --transport=oci --no-signature-verification
# boot to the login screen even when the installer ran in text mode
xconfig --startxonboot

%post --erroronfail
bootc switch --mutate-in-place --transport registry @IMAGE_REF@
%end
