import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from niruvi._version import __app_name__
from niruvi.utils import get_icon
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextBrowser,
    QDialogButtonBox, QWidget, QTabWidget,
    QListWidget, QListWidgetItem, QSplitter,
)


_GPL3_TEXT = """                    GNU GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007

 Copyright (C) 2007 Free Software Foundation, Inc. <https://fsf.org/>
 Everyone is permitted to copy and distribute verbatim copies
 of this license document, but changing it is not allowed.

                            Preamble

  The GNU General Public License is a free, copyleft license for
software and other kinds of works.

  The licenses for most software and other practical works are designed
to take away your freedom to share and change the works.  By contrast,
the GNU General Public License is intended to guarantee your freedom to
share and change all versions of a program--to make sure it remains free
software for all its users.  We, the Free Software Foundation, use the
GNU General Public License for most of our software; it applies also to
any other work released this way by its authors.  You can apply it to
your programs, too.

  When we speak of free software, we are referring to freedom, not
price.  Our General Public Licenses are designed to make sure that you
have the freedom to distribute copies of free software (and charge for
them if you wish), that you receive source code or can get it if you
want it, that you can change the software or use pieces of it in new
free programs, and that you know you can do these things.

  To protect your rights, we need to prevent others from denying you
these rights or asking you to surrender the rights.  Therefore, you have
certain responsibilities if you distribute copies of the software, or if
you modify it: responsibilities to respect the freedom of others.

  For example, if you distribute copies of such a program, whether
gratis or for a fee, you must pass on to the recipients the same
freedoms that you received.  You must make sure that they, too, receive
or can get the source code.  And you must show them these terms so they
know their rights.

  Developers that use the GNU GPL protect your rights with two steps:
(1) assert copyright on the software, and (2) offer you this License
giving you legal permission to copy, distribute and/or modify it.

  For the developers' and authors' protection, the GPL clearly explains
that there is no warranty for this free software.  For both users' and
authors' sake, the GPL requires that modified versions be marked as
changed, so that their problems will not be attributed erroneously to
authors of previous versions.

  Some devices are designed to deny users access to install or run
modified versions of the software inside them, although the manufacturer
can do so.  This is fundamentally incompatible with the aim of
protecting users' freedom to change the software.  The systematic
pattern of such abuse occurs in the area of products for individuals to
use, which is precisely where it is most unacceptable.  Therefore, we
have designed this version of the GPL to prohibit the practice for those
products.  If such problems arise substantially in other domains, we
stand ready to extend this provision to those domains in future versions
of the GPL, as needed to protect the freedom of users.

  Finally, every program is threatened constantly by software patents.
States should not allow patents to restrict development and use of
software on general-purpose computers, but in those that do, we wish to
avoid the special danger that patents applied to a free program could
make it effectively proprietary.  To prevent this, the GPL assures that
patents cannot be used to render the program non-free.

  The precise terms and conditions for copying, distribution and
modification follow.

                       TERMS AND CONDITIONS

  0. Definitions.

  \"This License\" refers to version 3 of the GNU General Public License.

  \"Copyright\" also means copyright-like laws that apply to other kinds of
works, such as semiconductor masks.

  \"The Program\" refers to any copyrightable work licensed under this
License.  Each licensee is addressed as \"you\".  \"Licensees\" and
\"recipients\" may be individuals or organizations.

  To \"modify\" a work means to copy from or adapt all or part of the work
in a fashion requiring copyright permission, other than the making of an
exact copy.  The resulting work is called a \"modified version\" of the
earlier work or a work \"based on\" the earlier work.

  A \"covered work\" means either the unmodified Program or a work based
on the Program.

  To \"propagate\" a work means to do anything with it that, without
permission, would make you directly or secondarily liable for
infringement under applicable copyright law, except executing it on a
computer or modifying a private copy.  Propagation includes copying,
distribution (with or without modification), making available to the
public, and in some countries other activities as well.

  To \"convey\" a work means any kind of propagation that enables other
parties to make or receive copies.  Mere interaction with a user through
a computer network, with no transfer of a copy, is not conveying.

  An interactive user interface displays \"Appropriate Legal Notices\"
to the extent that it includes a convenient and prominently visible
feature that (1) displays an appropriate copyright notice, and (2)
tells the user that there is no warranty for the program (except to the
extent that warranties are provided), that licensees may convey the
work under this License, and how to view a copy of this License.  If
the interface presents a list of user commands or options, such as a
menu, a prominent item in the list meets this criterion.

  1. Source Code.

  The \"source code\" for a work means the preferred form of the work
for making modifications to it.  \"Object code\" means any non-source
form of a work.

  A \"Standard Interface\" means an interface that either is an official
standard defined by a recognized standards body, or, in the case of
interfaces specified for a particular programming language, one that
is widely used among developers working in that language.

  The \"System Libraries\" of an executable work include anything, other
than the work as a whole, that (a) is included in the normal form of
packaging a Major Component, but which is not part of that Major
Component, and (b) serves only to enable use of the work with that
Major Component, or to implement a Standard Interface for which an
implementation is available to the public in source code form.  A
\"Major Component\", in this context, means a major essential component
(kernel, window system, and so on) of the specific operating system
(if any) on which the executable work runs, or a compiler used to
produce the work, or an object code interpreter used to run it.

  The \"Corresponding Source\" for a work in object code form means all
the source code needed to generate, install, and (for an executable
work) run the object code and to modify the work, including scripts to
control those activities.  However, it does not include the work's
System Libraries, or general-purpose tools or generally available free
programs which are used unmodified in performing those activities but
which are not part of the work.  For example, Corresponding Source
includes interface definition files associated with source files for
the work, and the source code for shared libraries and dynamically
linked subprograms that the work is specifically designed to require,
such as by intimate data communication or control flow between those
subprograms and other parts of the work.

  The Corresponding Source need not include anything that users
can regenerate automatically from other parts of the Corresponding
Source.

  The Corresponding Source for a work in source code form is that
same work.

  2. Basic Permissions.

  All rights granted under this License are granted for the term of
copyright on the Program, and are irrevocable provided the stated
conditions are met.  This License explicitly affirms your unlimited
permission to run the unmodified Program.  The output from running a
covered work is covered by this License only if the output, given its
content, constitutes a covered work.  This License acknowledges your
rights of fair use or other equivalent, as provided by copyright law.

  You may make, run and propagate covered works that you do not
convey, without conditions so long as your license otherwise remains
in force.  You may convey covered works to others for the sole purpose
of having them make modifications exclusively for you, or provide you
with facilities for running those works, provided that you comply with
the terms of this License in conveying all material for which you do
not control copyright.  Those thus making or running the covered works
for you must do so exclusively on your behalf, under your direction
and control, on terms that prohibit them from making any copies of
your copyrighted material outside their relationship with you.

  Conveying under any other circumstances is permitted solely under
the conditions stated below.  Sublicensing is not allowed; section 10
makes it unnecessary.

  3. Protecting Users' Legal Rights From Anti-Circumvention Law.

  No covered work shall be deemed part of an effective technological
measure under any applicable law fulfilling obligations under article
11 of the WIPO copyright treaty adopted on 20 December 1996, or
similar laws prohibiting or restricting circumvention of such
measures.

  When you convey a covered work, you waive any legal power to forbid
circumvention of technological measures to the extent such circumvention
is effected by exercising rights under this License with respect to
the covered work, and you disclaim any intention to limit operation or
modification of the work as a means of enforcing, against the work's
users, your or third parties' legal rights to forbid circumvention of
technological measures.

  4. Conveying Verbatim Copies.

  You may convey verbatim copies of the Program's source code as you
receive it, in any medium, provided that you conspicuously and
appropriately publish on each copy an appropriate copyright notice;
keep intact all notices stating that this License and any
non-permissive terms added in accord with section 7 apply to the code;
keep intact all notices of the absence of any warranty; and give all
recipients a copy of this License along with the Program.

  You may charge any price or no price for each copy that you convey,
and you may offer support or warranty protection for a fee.

  5. Conveying Modified Source Versions.

  You may convey a work based on the Program, or the modifications to
produce it from the Program, in the form of source code under the
terms of section 4, provided that you also meet all of these conditions:

    a) The work must carry prominent notices stating that you modified
    it, and giving a relevant date.

    b) The work must carry prominent notices stating that it is
    released under this License and any conditions added under section
    7.  This requirement modifies the requirement in section 4 to
    \"keep intact all notices\".

    c) You must license the entire work, as a whole, under this
    License to anyone who comes into possession of a copy.  This
    License will therefore apply, along with any applicable section 7
    additional terms, to the whole of the work, and all its parts,
    regardless of how they are packaged.  This License gives no
    permission to license the work in any other way, but it does not
    invalidate such permission if you have separately received it.

    d) If the work has interactive user interfaces, each must display
    Appropriate Legal Notices; however, if the Program has interactive
    interfaces that do not display Appropriate Legal Notices, your
    work need not make them do so.

  A compilation of a covered work with other separate and independent
works, which are not by their nature extensions of the covered work,
and which are not combined with it such as to form a larger program,
in or on a volume of a storage or distribution medium, is called an
\"aggregate\" if the compilation and its resulting copyright are not
used to limit the access or legal rights of the compilation's users
beyond what the individual works permit.  Inclusion of a covered work
in an aggregate does not cause this License to apply to the other
parts of the aggregate.

  6. Conveying Non-Source Forms.

  You may convey a covered work in object code form under the terms
of sections 4 and 5, provided that you also convey the
machine-readable Corresponding Source under the terms of this License,
in one of these ways:

    a) Convey the object code in, or embodied in, a physical product
    (including a physical distribution medium), accompanied by the
    Corresponding Source fixed on a durable physical medium
    customarily used for software interchange.

    b) Convey the object code in, or embodied in, a physical product
    (including a physical distribution medium), accompanied by a
    written offer, valid for at least three years and valid for as
    long as you offer spare parts or customer support for that product
    model, to give anyone who possesses the object code either (1) a
    copy of the Corresponding Source for all the software in the
    product that is covered by this License, on a durable physical
    medium customarily used for software interchange, for a price no
    more than your reasonable cost of physically performing this
    conveying of source, or (2) access to copy the
    Corresponding Source from a network server at no charge.

    c) Convey individual copies of the object code with a copy of the
    written offer to provide the Corresponding Source.  This
    alternative is allowed only occasionally and noncommercially, and
    only if you received the object code with such an offer, in accord
    with subsection 6b.

    d) Convey the object code by offering access from a designated
    place (gratis or for a charge), and offer equivalent access to the
    Corresponding Source in the same way through the same place at no
    further charge.  You need not require recipients to copy the
    Corresponding Source along with the object code.  If the place to
    copy the object code is a network server, the Corresponding Source
    may be on a different server (operated by you or a third party)
    that supports equivalent copying facilities, provided you maintain
    clear directions next to the object code saying where to find the
    Corresponding Source.  Regardless of what server hosts the
    Corresponding Source, you remain obligated to ensure that it is
    available for as long as needed to satisfy these requirements.

    e) Convey the object code using peer-to-peer transmission, provided
    you inform other peers where the object code and Corresponding
    Source of the work are being offered to the general public at no
    charge under subsection 6d.

  A separable portion of the object code, whose source code is excluded
from the Corresponding Source as a System Library, need not be
included in conveying the object code work.

  A \"User Product\" is either (1) a \"consumer product\", which means any
tangible personal property which is normally used for personal, family,
or household purposes, or (2) anything designed or sold for incorporation
into a dwelling.  In determining whether a product is a consumer product,
doubtful cases shall be resolved in favor of coverage.  For a particular
product received by a particular user, \"normally used\" refers to a
typical or common use of that class of product, regardless of the status
of the particular user or of the way in which the particular user
actually uses, or expects or is expected to use, the product.  A product
is a consumer product regardless of whether the product has substantial
commercial, industrial or non-consumer uses, unless such uses represent
the only significant mode of use of the product.

  \"Installation Information\" for a User Product means any methods,
procedures, authorization keys, or other information required to install
and execute modified versions of a covered work in that User Product from
a modified version of its Corresponding Source.  The information must
suffice to ensure that the continued functioning of the modified object
code is in no case prevented or interfered with solely because
modification has been made.

  If you convey an object code work under this section in, or with, or
specifically for use in, a User Product, and the conveying occurs as
part of a transaction in which the right of possession and use of the
User Product is transferred to the recipient in perpetuity or for a
fixed term (regardless of how the transaction is characterized), the
Corresponding Source conveyed under this section must be accompanied
by the Installation Information.  But this requirement does not apply
if neither you nor any third party retains the ability to install
modified object code on the User Product (for example, the work has
been installed in ROM).

  The requirement to provide Installation Information does not include a
requirement to continue to provide support service, warranty, or updates
for a work that has been modified or installed by the recipient, or for
the User Product in which it has been modified or installed.  Access to a
network may be denied when the modification itself materially and
adversely affects the operation of the network or violates the rules and
protocols for communication across the network.

  Corresponding Source conveyed, and Installation Information provided,
in accord with this section must be in a format that is publicly
documented (and with an implementation available to the public in
source code form), and must require no special password or key for
unpacking, reading or copying.

  7. Additional Terms.

  \"Additional permissions\" are terms that supplement the terms of this
License by making exceptions from one or more of its conditions.
Additional permissions that are applicable to the entire Program shall
be treated as though they were included in this License, to the extent
that they are valid under applicable law.  If additional permissions
apply only to part of the Program, that part may be used separately
under those permissions, but the entire Program remains governed by
this License without regard to the additional permissions.

  When you convey a copy of a covered work, you may at your option
remove any additional permissions from that copy, or from any part of
it.  (Additional permissions may be written to require their own
removal in certain cases when you modify the work.)  You may place
additional permissions on material, added by you to a covered work,
for which you have or can give appropriate copyright permission.

  Notwithstanding any other provision of this License, for material you
add to a covered work, you may (if authorized by the copyright holders of
that material) supplement the terms of this License with terms:

    a) Disclaiming warranty or limiting liability differently from the
    terms of sections 15 and 16 of this License; or

    b) Requiring preservation of specified reasonable legal notices or
    author attributions in that material or in the Appropriate Legal
    Notices displayed by works containing it; or

    c) Prohibiting misrepresentation of the origin of that material, or
    requiring that modified versions of such material be marked in
    reasonable ways as different from the original version; or

    d) Limiting the use for publicity purposes of names of licensors or
    authors of the material; or

    e) Declining to grant rights under trademark law for use of some
    trade names, trademarks, or service marks; or

    f) Requiring indemnification of licensors and authors of that
    material by anyone who conveys the material (or modified versions of
    it) with contractual assumptions of liability to the recipient, for
    any liability that these contractual assumptions directly impose on
    those licensors and authors.

  All other non-permissive additional terms are considered \"further
restrictions\" within the meaning of section 10.  If the Program as you
received it, or any part of it, contains a notice stating that it is
governed by this License along with a term that is a further
restriction, you may remove that term.  If a license document contains
a further restriction but permits relicensing or conveying under this
License, you may add to a covered work material governed by the terms
of that license document, provided that the further restriction does
not survive such relicensing or conveying.

  If you add terms to a covered work in accord with this section, you
must place, in the relevant source files, a statement of the
additional terms that apply to those files, or a notice indicating
where to find the applicable terms.

  Additional terms, permissive or non-permissive, may be stated in the
form of a separately written license, or stated as exceptions;
the above requirements apply either way.

  8. Termination.

  You may not propagate or modify a covered work except as expressly
provided under this License.  Any attempt otherwise to propagate or
modify it is void, and will automatically terminate your rights under
this License (including any patent licenses granted under the third
paragraph of section 11).

  However, if you cease all violation of this License, then your
license from a particular copyright holder is reinstated (a)
provisionally, unless and until the copyright holder explicitly and
finally terminates your license, and (b) permanently, if the copyright
holder fails to notify you of the violation by some reasonable means
prior to 60 days after the cessation.

  Moreover, your license from a particular copyright holder is
reinstated permanently if the copyright holder notifies you of the
violation by some reasonable means, this is the first time you have
received notice of violation of this License (for any work) from that
copyright holder, and you cure the violation prior to 30 days after
your receipt of the notice.

  Termination of your rights under this section does not terminate the
licenses of parties who have received copies or rights from you under
this License.  If your rights have been terminated and not permanently
reinstated, you do not qualify to receive new licenses for the same
material under section 10.

  9. Acceptance Not Required for Having Copies.

  You are not required to accept this License in order to receive or
run a copy of the Program.  Ancillary propagation of a covered work
occurring solely as a consequence of using peer-to-peer transmission
to receive a copy likewise does not require acceptance.  However,
nothing other than this License grants you permission to propagate or
modify any covered work.  These actions infringe copyright if you do
not accept this License.  Therefore, by modifying or propagating a
covered work, you indicate your acceptance of this License to do so.

  10. Automatic Licensing of Downstream Recipients.

  Each time you convey a covered work, the recipient automatically
receives a license from the original licensors, to run, modify and
propagate that work, subject to this License.  You are not responsible
for enforcing compliance by third parties with this License.

  An \"entity transaction\" is a transaction transferring control of an
organization, or substantially all assets of one, or subdividing an
organization, or merging organizations.  If propagation of a covered
work results from an entity transaction, each party to that
transaction who receives a copy of the work also receives whatever
licenses to the work the party's predecessor in interest had or could
give under the previous paragraph, plus a right to possession of the
Corresponding Source of the work from the predecessor in interest, if
the predecessor has it or can get it with reasonable efforts.

  You may not impose any further restrictions on the exercise of the
rights granted or affirmed under this License.  For example, you may
not impose a license fee, royalty, or other charge for exercise of
rights granted under this License, and you may not initiate litigation
(including a cross-claim or counterclaim in a lawsuit) alleging that
any patent claim is infringed by making, using, selling, offering for
sale, or importing the Program or any portion of it.

  11. Patents.

  A \"contributor\" is a copyright holder who authorizes use under this
License of the Program or a work on which the Program is based.  The
work thus licensed is called the contributor's \"contributor version\".

  A contributor's \"essential patent claims\" are all patent claims
owned or controlled by the contributor, whether already acquired or
hereafter acquired, that would be infringed by some manner, permitted
by this License, of making, using, or selling its contributor version,
but do not include claims that would be infringed only as a
consequence of further modification of the contributor version.  For
purposes of this definition, \"control\" includes the right to grant
patent sublicenses in a manner consistent with the requirements of
this License.

  Each contributor grants you a non-exclusive, worldwide, royalty-free
patent license under the contributor's essential patent claims, to
make, use, sell, offer for sale, import and otherwise run, modify and
propagate the contents of its contributor version.

  In the following three paragraphs, a \"patent license\" is any express
agreement or commitment, however denominated, not to enforce a patent
(such as an express permission to practice a patent or covenant not to
sue for patent infringement).  To \"grant\" such a patent license to a
party means to make such an agreement or commitment not to enforce a
patent against the party.

  If you convey a covered work, knowingly relying on a patent license,
and the Corresponding Source of the work is not available for anyone
to copy, free of charge and under the terms of this License, through a
publicly available network server or other readily accessible means,
then you must either (1) cause the Corresponding Source to be so
available, or (2) arrange to deprive yourself of the benefit of the
patent license for this particular work, or (3) arrange, in a manner
consistent with the requirements of this License, to extend the patent
license to downstream recipients.  \"Knowingly relying\" means you have
actual knowledge that, but for the patent license, your conveying the
covered work in a country, or your recipient's use of the covered work
in a country, would infringe one or more identifiable patents in that
country that you have reason to believe are valid.

  If, pursuant to or in connection with a single transaction or
arrangement, you convey, or propagate by procuring conveyance of, a
covered work, and grant a patent license to some of the parties
receiving the covered work authorizing them to use, propagate, modify
or convey a specific copy of the covered work, then the patent license
you grant is automatically extended to all recipients of the covered
work and works based on it.

  A patent license is \"discriminatory\" if it does not include within
the scope of its coverage, prohibits the exercise of, or is
conditioned on the non-exercise of one or more of the rights that are
specifically granted under this License.  You may not convey a covered
work if you are a party to an arrangement with a third party that is
in the business of distributing software, under which you make payment
to the third party based on the extent of your activity of conveying
the work, and under which the third party grants, to any of the
parties who would receive the covered work from you, a discriminatory
patent license (a) in connection with copies of the covered work
conveyed by you (or copies made from those copies), or (b) primarily
for and in connection with specific products or compilations that
contain the covered work, unless you entered into that arrangement,
or that patent license was granted, prior to 28 March 2007.

  Nothing in this License shall be construed as excluding or limiting
any implied license or other defenses to infringement that may
otherwise be available to you under applicable patent law.

  12. No Surrender of Others' Freedom.

  If conditions are imposed on you (whether by court order, agreement or
otherwise) that contradict the conditions of this License, they do not
excuse you from the conditions of this License.  If you cannot convey a
covered work so as to satisfy simultaneously your obligations under this
License and any other pertinent obligations, then as a consequence you may
not convey it at all.  For example, if you agree to terms that obligate you
to collect a royalty for further conveying from those to whom you convey
the Program, the only way you could satisfy both those terms and this
License would be to refrain entirely from conveying the Program.

  13. Use with the GNU Affero General Public License.

  Notwithstanding any other provision of this License, you have
permission to link or combine any covered work with a work licensed
under version 3 of the GNU Affero General Public License into a single
combined work, and to convey the resulting work.  The terms of this
License will continue to apply to the part which is the covered work,
but the special requirements of the GNU Affero General Public License,
section 13, concerning interaction through a network will apply to the
combination as such.

  14. Revised Versions of this License.

  The Free Software Foundation may publish revised and/or new versions of
the GNU General Public License from time to time.  Such new versions will
be similar in spirit to the present version, but may differ in detail to
address new problems or concerns.

  Each version is given a distinguishing version number.  If the
Program specifies that a certain numbered version of the GNU General
Public License \"or any later version\" applies to it, you have the
option of following the terms and conditions either of that numbered
version or of any later version published by the Free Software
Foundation.  If the Program does not specify a version number of the
GNU General Public License, you may choose any version ever published
by the Free Software Foundation.

  If the Program specifies that a proxy can decide which future
versions of the GNU General Public License can be used, that proxy's
public statement of acceptance of a version permanently authorizes you
to choose that version for the Program.

  Later license versions may give you additional or different
permissions.  However, no additional obligations are imposed on any
author or copyright holder as a result of your choosing to follow a
later version.

  15. Disclaimer of Warranty.

  THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY
APPLICABLE LAW.  EXCEPT WHEN OTHERWISE STATED IN WRITING THE COPYRIGHT
HOLDERS AND/OR OTHER PARTIES PROVIDE THE PROGRAM \"AS IS\" WITHOUT WARRANTY
OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT LIMITED TO,
THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
PURPOSE.  THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE OF THE PROGRAM
IS WITH YOU.  SHOULD THE PROGRAM PROVE DEFECTIVE, YOU ASSUME THE COST OF
ALL NECESSARY SERVICING, REPAIR OR CORRECTION.

  16. Limitation of Liability.

  IN NO EVENT UNLESS REQUIRED BY APPLICABLE LAW OR AGREED TO IN WRITING
WILL ANY COPYRIGHT HOLDER, OR ANY OTHER PARTY WHO MODIFIES AND/OR CONVEYS
THE PROGRAM AS PERMITTED ABOVE, BE LIABLE TO YOU FOR DAMAGES, INCLUDING ANY
GENERAL, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE
USE OR INABILITY TO USE THE PROGRAM (INCLUDING BUT NOT LIMITED TO LOSS OF
DATA OR DATA BEING RENDERED INACCURATE OR LOSSES SUSTAINED BY YOU OR THIRD
PARTIES OR A FAILURE OF THE PROGRAM TO OPERATE WITH ANY OTHER PROGRAMS),
EVEN IF SUCH HOLDER OR OTHER PARTY HAS BEEN ADVISED OF THE POSSIBILITY OF
SUCH DAMAGES.

  17. Interpretation of Sections 15 and 16.

  If the disclaimer of warranty and limitation of liability provided
above cannot be given local legal effect according to their terms,
reviewing courts shall apply local law that most closely approximates
an absolute waiver of all civil liability in connection with the
Program, unless a warranty or assumption of liability accompanies a
copy of the Program in return for a fee.

                     END OF TERMS AND CONDITIONS

            How to Apply These Terms to Your New Programs

  If you develop a new program, and you want it to be of the greatest
possible use to the public, the best way to achieve this is to make it
free software which everyone can redistribute and change under these terms.

  To do so, attach the following notices to the program.  It is safest
to attach them to the start of each source file to most effectively
state the exclusion of warranty; and each file should have at least
the \"copyright\" line and a pointer to where the full notice is found.

    <one line to give the program's name and a brief idea of what it does.>
    Copyright (C) <year>  <name of author>

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.

Also add information on how to contact you by electronic and paper mail.

  If the program does terminal interaction, make it output a short
notice like this when it starts in an interactive mode:

    <program>  Copyright (C) <year>  <name of author>
    This program comes with ABSOLUTELY NO WARRANTY; for details type `show w'.
    This is free software, and you are welcome to redistribute it
    under certain conditions; type `show c' for details.

The hypothetical commands `show w' and `show c' should show the appropriate
parts of the General Public License.  Of course, your program's commands
might be different; for a GUI interface, you would use an \"about box\".

  You should also get your employer (if you work as a programmer) or school,
if any, to sign a \"copyright disclaimer\" for the program, if necessary.
For more information on this, and how to apply and follow the GNU GPL, see
<https://www.gnu.org/licenses/>.

  The GNU General Public License does not permit incorporating your program
into proprietary programs.  If your program is a subroutine library, you
may consider it more useful to permit linking proprietary applications with
the library.  If this is what you want to do, use the GNU Lesser General
Public License instead of this License.  See <https://www.gnu.org/licenses/>.
"""


class LicenseDialog(QDialog):
    """Displays the application's own license (GPL-3.0)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("License")
        self.setMinimumSize(600, 450)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setFont(QFont("monospace", 9))

        appdir = os.environ.get("APPDIR", "")
        candidates = [
            Path(__file__).resolve().parent.parent / "LICENSE",
            Path(__file__).resolve().parent.parent.parent / "LICENSE",
            Path(__file__).resolve().parent / "LICENSE",
            Path(os.getcwd()) / "LICENSE",
            Path.home() / ".local" / "share" / __app_name__ / "LICENSE",
            Path("/usr/share") / __app_name__ / "LICENSE",
        ]
        if appdir:
            candidates.append(Path(appdir) / "LICENSE")
        text = _GPL3_TEXT
        for p in candidates:
            if p.exists():
                text = p.read_text()
                break
        browser.setPlainText(text)
        layout.addWidget(browser, 1)

        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(self.accept)
        layout.addWidget(btn)


class HelpDialog(QDialog):
    """Comprehensive help dialog for the Niruvi application."""

    def __init__(self, parent=None, initial_page=None):
        super().__init__(parent)
        self.setWindowTitle("Niruvi Help")
        self.setMinimumSize(720, 540)
        self._init_ui(initial_page)

    def _init_ui(self, initial_page=None):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Sidebar navigation ──
        nav = QListWidget()
        nav.setFixedWidth(180)
        nav.setCurrentRow(0)
        nav.setStyleSheet(
            "QListWidget { border: none; }"
            "QListWidget::item { padding: 8px 12px; }"
            "QListWidget::item:selected { background: palette(highlight); color: palette(highlighted-text); }"
        )

        pages = [
            ("Welcome", self._page_welcome, "go-home"),
            ("Installing Apps", self._page_install, "list-add"),
            ("Managing Apps", self._page_manage, "preferences-other"),
            ("App Info", self._page_appinfo, "dialog-information"),
            ("Updates", self._page_updates, "emblem-downloads"),
            ("Removing Apps", self._page_uninstall, "edit-delete"),
            ("Building AppImages", self._page_build, "applications-utilities"),
            ("Self-Installing Format", self._page_selfinstall, "package-x-generic"),
            ("Silent / CLI Mode", self._page_cli, "utilities-terminal"),
            ("Settings", self._page_settings, "preferences-system"),
            ("Security Scanner", self._page_security, "dialog-warning"),
            ("Troubleshooting", self._page_trouble, "dialog-information"),
            ("License", self._page_license, "emblem-documents"),
        ]
        self._page_map = pages
        for title, _, icon_name in pages:
            nav.addItem(QListWidgetItem(get_icon(icon_name, "help-contents"), title))
        nav.currentRowChanged.connect(self._on_page_changed)
        splitter.addWidget(nav)

        # ── Content pane ──
        self.content = QTextBrowser()
        self.content.setOpenExternalLinks(True)
        self.content.setFont(QFont("sans-serif", 10))
        self.content.setStyleSheet("QTextBrowser { padding: 12px; }")
        splitter.addWidget(self.content)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, 1)

        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(self.accept)
        layout.addWidget(btn)

        if initial_page:
            for i, (title, _, _) in enumerate(pages):
                if title == initial_page:
                    nav.setCurrentRow(i)
                    self._show_page(i)
                    return
        self._show_page(0)

    def _on_page_changed(self, idx: int):
        self._show_page(idx)

    def _show_page(self, idx: int):
        if 0 <= idx < len(self._page_map):
            _, renderer, _ = self._page_map[idx]
            self.content.setHtml(renderer())

    @staticmethod
    def _page_welcome():
        return """<h2>Welcome to Niruvi</h2>
<p>Niruvi is a universal Linux AppImage manager that lets you <b>install</b>, <b>update</b>,
<b>uninstall</b>, and <b>build</b> AppImage applications through a clean graphical interface.</p>

<h3>Key capabilities</h3>
<ul>
<li><b>Install</b> &mdash; Drag-and-drop or browse for an AppImage to install it into a managed directory with desktop integration.</li>
<li><b>Updates</b> &mdash; Per-app update URL configuration with automatic detection from GitHub and GitLab repository URLs. Background auto-update checks with desktop notifications.</li>
<li><b>Build</b> &mdash; Create AppImages from DEB, RPM, tar archives, or project folders with optional self-installing wizard.</li>
<li><b>Desktop integration</b> &mdash; Automatic .desktop entries, shortcuts, and icon theme installation.</li>
<li><b>Safety</b> &mdash; Backup/rollback on updates, SHA256 verification, and built-in security scanning.</li>
<li><b>Per-app customization</b> &mdash; Override display names, custom icons, environment variables, and command-line arguments per application.</li>
<li><b>Side-by-side installs</b> &mdash; Install multiple versions of the same app alongside each other.</li>
<li><b>Import/Export</b> &mdash; Export and import your app list as JSON.</li>
</ul>

<h3>Quick start</h3>
<ol>
<li>Launch Niruvi from your application menu or run <code>niruvi</code> in a terminal.</li>
<li>Drop an <code>.AppImage</code> file onto the window, or click <b>Install AppImage</b>.</li>
<li>Follow the on-screen wizard to complete installation.</li>
<li>The app appears in your launcher and on the installed list.</li>
</ol>

<p>For command-line usage, see the <b>Silent / CLI Mode</b> section.</p>"""

    @staticmethod
    def _page_install():
        return """<h2>Installing Apps</h2>

<h3>Drag and drop</h3>
<p>Drag an <code>.AppImage</code> file from your file manager onto Niruvi's main window.
A drop zone appears &mdash; release to start installation.</p>

<h3>Using the Install button</h3>
<ol>
<li>Click <b>Install AppImage</b> on the main window.</li>
<li>Browse to the <code>.AppImage</code> file and select it.</li>
<li>The installation wizard shows you metadata about the app (name, icon, description).</li>
<li>Choose installation options such as desktop entry creation, shortcut, portable folders, and icon theme integration.</li>
<li>Click <b>Install</b> &mdash; the AppImage is extracted under <code>~/Applications/APPNAME/</code>.</li>
</ol>

<h3>AppImage validation</h3>
<p>Every AppImage is validated before installation to ensure it has a valid format and proper metadata.</p>

<h3>Already installed?</h3>
<p>If an app is already installed, you are offered options to <b>Re-integrate</b> or <b>Remove</b> the existing installation first.</p>"""

    @staticmethod
    def _page_manage():
        return """<h2>Managing Apps</h2>

<h3>App list</h3>
<p>Installed apps appear in the main list with name, version, and icon. Use the <b>Search</b> box
to filter by name, and the <b>Sort</b> dropdown to order by name, version, size, or install date.</p>

<h3>Right-click context menu</h3>
<ul>
<li><b>App Info</b> &mdash; Open the detailed App Info dialog to view metadata and customize the app.</li>
<li><b>Run</b> &mdash; Launch the application immediately.</li>
<li><b>Update</b> &mdash; Replace the app with a newer AppImage file. A backup is created automatically.</li>
<li><b>Check for Updates</b> &mdash; Check the configured update URL for newer versions.</li>
<li><b>Uninstall</b> &mdash; Remove the app and all its files (desktop entry, shortcut, data).</li>
<li><b>Open Folder</b> &mdash; Open the app's install directory in your file manager.</li>
<li><b>Create / Remove Desktop Shortcut</b> &mdash; Toggle a desktop launcher icon.</li>
</ul>

<h3>Side-by-side installs</h3>
<p>When you install an app that is already installed, you can choose <b>Install Side-by-Side</b>
to keep both versions. The new copy gets a numbered suffix (e.g. <code>MyApp-2</code>).</p>

<h3>Import / Export</h3>
<p>Use <b>File &rarr; Export App List</b> to save your installation registry as a JSON file.
Use <b>File &rarr; Import App List</b> to restore apps from a previously exported file.
Duplicate apps are skipped during import.</p>

<h3>Desktop integration</h3>
<p>Niruvi automatically creates a <code>.desktop</code> entry on install so the app appears in your
system application menu. Icons are installed into the XDG icon theme for cross-desktop compatibility.</p>"""

    @staticmethod
    def _page_appinfo():
        return """<h2>App Info</h2>
<p>Double-click an app or select <b>App Info</b> from the right-click menu to open the
App Info dialog. This is the central place to view and customize your installed apps.</p>

<h3>Sections</h3>
<ul>
<li><b>Details</b> &mdash; App name, path, size, install date, architecture, SHA256 hash, desktop entry path.</li>
<li><b>Customization</b> &mdash; Override the display name, choose a custom icon, set command-line arguments, and manage environment variables.</li>
<li><b>Updates</b> &mdash; Configure an update URL (GitHub repo, GitLab project, or direct download), choose update channel (stable/beta/nightly), enable background auto-updates.</li>
<li><b>Files</b> &mdash; Browse the app's install directory in a file tree.</li>
</ul>

<h3>Customizing per-app behavior</h3>
<ul>
<li><b>Display name</b> &mdash; Override how the app appears in the list.</li>
<li><b>Custom icon</b> &mdash; Pick a PNG/SVG/XPM file to use as the app's icon.</li>
<li><b>Run arguments</b> &mdash; Arguments passed to the app when launched from Niruvi (e.g. <code>--verbose</code>).</li>
<li><b>Environment variables</b> &mdash; Set variables that are exported before launching the app (e.g. <code>LANG=en_US.UTF-8</code>).</li>
</ul>

<h3>Actions bar</h3>
<p>At the bottom of the dialog, you can <b>Run</b> or <b>Uninstall</b> the app directly.</p>"""

    @staticmethod
    def _page_updates():
        return """<h2>Updates</h2>
<p>Niruvi supports update checking for both itself and installed apps.</p>

<h3>Niruvi self-update</h3>
<p>Use <b>Tools &rarr; Check for Niruvi Updates</b> to check if a new version of Niruvi is
available. Updates are downloaded from the GitHub releases page and verified by SHA256.</p>

<h3>Per-app update URLs</h3>
<p>Each installed app can have an update URL configured in its App Info dialog.
Niruvi supports three types of update sources:</p>

<ul>
<li><b>GitHub repository</b> &mdash; Paste a GitHub repo URL (e.g. <code>https://github.com/user/repo</code>).
Niruvi automatically queries the GitHub API to find the latest release and download
the AppImage asset matching your system architecture.</li>
<li><b>GitLab project</b> &mdash; Paste a GitLab project URL. Niruvi uses the GitLab API to
find the latest release.</li>
<li><b>Direct URL</b> &mdash; A direct download link to an AppImage. Niruvi checks for
filename-based version detection.</li>
</ul>

<h3>Checking for updates</h3>
<p>Use <b>Check for Updates</b> in the App Info dialog to manually check a single app.
Use <b>Tools &rarr; Check All Apps for Updates</b> to check all configured apps at once.
If a newer version is found, you'll be prompted to download and install it.</p>

<h3>Background auto-updates</h3>
<p>When enabled in Settings, Niruvi periodically checks all apps that have
<b>Auto-update in background</b> enabled (configured per-app in the App Info dialog).
On finding an update, a desktop notification is shown (if the system supports it),
or a dialog prompts you to install.</p>

<h3>Update channels</h3>
<p>Each app can be assigned an update channel: <b>stable</b> (default), <b>beta</b>, or
<b>nightly</b>. This affects which release GitHub/GitLab resolves to when the
API supports it.</p>"""

    @staticmethod
    def _page_uninstall():
        return """<h2>Removing Apps</h2>

<h3>Via the app list</h3>
<ol>
<li>Right-click the app in the list.</li>
<li>Choose <b>Uninstall</b> from the context menu.</li>
<li>Confirm the uninstall dialog.</li>
</ol>

<h3>Via the command line</h3>
<pre>niruvi --uninstall APP_NAME</pre>

<h3>What gets removed</h3>
<ul>
<li>The app directory under <code>~/Applications/APP_NAME/</code></li>
<li>The <code>.desktop</code> file in <code>~/.local/share/applications/</code></li>
<li>The desktop shortcut file</li>
<li>Portable <code>.home</code> and <code>.config</code> folders if they exist</li>
<li>The installation registry entry</li>
</ul>

<p>⚠ Data in user folders such as <code>~/Documents</code> is not affected.</p>"""

    @staticmethod
    def _page_build():
        return """<h2>Building AppImages</h2>

<p>Niruvi can build AppImage packages from DEB, RPM, tar archives, or project folders.
This is useful for repackaging traditional Linux packages into portable AppImages.</p>

<h3>Source types</h3>
<p>Choose between two source types:</p>
<ul>
<li><b>Package file</b> &mdash; Extract a DEB, RPM, or tar archive and repackage it as an AppImage.</li>
<li><b>Project folder</b> &mdash; Select a local project directory. Contents are copied directly into the AppDir, making it easy to package your own applications.</li>
</ul>

<h3>Basic build</h3>
<ol>
<li>Click <b>Build AppImage</b> in the Tools menu or on the toolbar.</li>
<li>Select a source type: <b>Package file</b> or <b>Project folder</b>.</li>
<li>Browse to select the source file or folder.</li>
<li>Set the app name and version (auto-detected if left empty).</li>
<li>Choose an output directory.</li>
<li>Click <b>Build AppImage</b>.</li>
</ol>

<h3>Post-build verification</h3>
<p>After building, Niruvi verifies the output AppImage — checks the ELF header, confirms
it's executable, runs <code>--version</code>, and shows a detailed build summary.</p>

<h3>Self-Installing AppImages</h3>
<p>Enable <b>Self-Installing AppImage</b> to create an AppImage that installs itself
on first run — ideal for applications that need desktop integration.</p>

<p>When self-installing is enabled, these options are available:</p>
<ul>
<li><b>Brand name</b> &mdash; Display name in installer dialogs.</li>
<li><b>License file</b> &mdash; EULA shown during installation.</li>
<li><b>Pre/Post-install scripts</b> &mdash; Shell scripts run before/after extraction.</li>
<li><b>Components</b> &mdash; Optional feature sets users can choose.</li>
<li><b>Update URL</b> &mdash; Remote JSON manifest for automatic updates.</li>
<li><b>Welcome / Finish text</b> &mdash; Custom installer messages.</li>
<li><b>Rollback, Silent mode, Launch prompt</b> &mdash; Installer behavior options.</li>
</ul>"""

    @staticmethod
    def _page_selfinstall():
        return """<h2>Self-Installing Format</h2>

<p>Standard AppImages are fully portable &mdash; they run anywhere without installation.
A <b>Self-Installing AppImage</b> prompts the user to install it on first run, then
behaves like a traditionally installed application with desktop integration.</p>

<h3>How it works</h3>
<ol>
<li>User downloads the AppImage and makes it executable (<code>chmod +x</code>).</li>
<li>Running the AppImage presents a welcome screen with <b>Install</b> and <b>Cancel</b> options.</li>
<li>Upon install, the AppImage extracts itself to <code>~/Applications/APP_NAME/</code>.</li>
<li>A <code>.desktop</code> entry is created so the app appears in the system launcher.</li>
<li>The AppImage can optionally be hidden or removed after installation.</li>
</ol>

<h3>CLI flags for self-installing AppImages</h3>
<pre>MyApp.AppImage --help           Show CLI usage
MyApp.AppImage --install        Interactive install
MyApp.AppImage --unattended     Silent install with defaults</pre>

<p>Niruvi itself is distributed as a self-installing AppImage.</p>"""

    @staticmethod
    def _page_cli():
        return """<h2>Silent / CLI Mode</h2>

<p>Niruvi supports command-line operations for scripting and headless environments.</p>

<h3>Commands</h3>
<pre>niruvi                          Launch the GUI
niruvi --install PATH           Silent install (no GUI)
niruvi --uninstall APP_NAME     Remove an installed app
niruvi --list                   List all installed apps
niruvi --update-all             Check all apps for updates in terminal
niruvi --update-check APP       Check a specific app for updates
niruvi --is-installed PATH      Check if an AppImage is installed
niruvi --version                Show version
niruvi PATH.AppImage            Launch and open a specific AppImage</pre>

<h3>Examples</h3>
<pre>niruvi --install MyApp.AppImage
niruvi --uninstall MyApp
niruvi --list
niruvi --update-all
niruvi --update-check MyApp
niruvi --is-installed /path/to/MyApp.AppImage</pre>

<h3>Self-installing silent mode</h3>
<p>AppImages built with the self-installing format support:</p>
<pre>MyApp.AppImage --help           Show CLI usage
MyApp.AppImage --install        Interactive install
MyApp.AppImage --unattended     Silent install with defaults
MyApp.AppImage --update         Check for and apply updates
MyApp.AppImage --check-updates  Silently check for updates</pre>

<p>The <code>--unattended</code> flag installs to the default directory (<code>~/Applications/APP_NAME</code>),
accepts the license if present, and skips all interactive prompts.</p>"""

    @staticmethod
    def _page_settings():
        return """<h2>Settings</h2>

<p>Configure Niruvi via <b>File &rarr; Settings</b>. Settings are saved to
<code>~/.config/niruvi/settings.json</code>.</p>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse;">
<tr style="background: palette(highlight); color: palette(highlighted-text);">
<th>Setting</th><th>Default</th><th>Description</th>
</tr>
<tr>
<td><code>install_dir</code></td><td><code>~/Applications</code></td>
<td>Directory where apps are installed</td>
</tr>
<tr>
<td><code>create_desktop</code></td><td><code>true</code></td>
<td>Create .desktop entry on install</td>
</tr>
<tr>
<td><code>create_shortcut</code></td><td><code>false</code></td>
<td>Create desktop shortcut on install</td>
</tr>
<tr>
<td><code>portable_home</code></td><td><code>false</code></td>
<td>Create <code>.home</code> folder on install</td>
</tr>
<tr>
<td><code>portable_config</code></td><td><code>false</code></td>
<td>Create <code>.config</code> folder on install</td>
</tr>
<tr>
<td><code>icon_in_theme</code></td><td><code>true</code></td>
<td>Install icon to XDG theme directory</td>
</tr>
<tr>
<td><code>auto_scan_before_install</code></td><td><code>true</code></td>
<td>Run security scan before every install</td>
</tr>
<tr>
<td><code>update_check_interval</code></td><td><code>weekly</code></td>
<td>How often to check for app updates in background (daily/weekly/monthly)</td>
</tr>
<tr>
<td><code>auto_update_apps</code></td><td><code>false</code></td>
<td>Enable background auto-update checks for apps with per-app auto-update enabled</td>
</tr>
</table>

<h3>Background Updates</h3>
<p>The <b>Background Updates</b> section controls periodic update checking. When enabled,
Niruvi checks all apps that have <b>Auto-update in background</b> turned on in their
App Info page. The check interval can be set to daily, weekly, or monthly.</p>
<p>When an update is found, Niruvi attempts to show a desktop notification. If the
system doesn't support tray notifications, a standard dialog is shown instead.</p>"""

    @staticmethod
    def _page_security():
        return """<h2>AppImage Validation</h2>

<p>Niruvi validates each AppImage before installation to verify the format,
architecture compatibility, and metadata integrity.</p>

<h3>What is checked</h3>
<ul>
<li><b>Format</b> &mdash; Verifies the AppImage uses a valid Type 1 or Type 2 format.</li>
<li><b>Architecture</b> &mdash; Confirms the binary architecture matches your system.</li>
<li><b>Metadata</b> &mdash; Reads embedded desktop file and icon data for proper integration.</li>
</ul>

<h3>What is checked</h3>
<ul>
<li>Embedded binaries or scripts in unexpected locations</li>
<li>Reverse shell or backdoor indicators</li>
<li>Suspicious network connections (hardcoded IPs, known malicious domains)</li>
<li>Unsafe file permissions</li>
<li>Unexpected SUID/setuid binaries</li>
<li>Fork bombs, <code>dd</code> overwrites, <code>exec</code> injection patterns</li>
</ul>

<h3>Self-scan</h3>
<p>Use <b>Help &rarr; Security Self-Check</b> to run a security scan on Niruvi's own
AppImage. This is useful after downloading a new version to verify its integrity.</p>

<h3>SHA256 verification</h3>
<p>Every AppImage is verified by SHA256 hash during installation and when applying
updates via the auto-updater. The hash is displayed in the security scan dialog.</p>"""

    @staticmethod
    def _page_trouble():
        return """<h2>Troubleshooting</h2>

<h3>AppImage won't run</h3>
<ul>
<li>Make sure it is executable: <code>chmod +x MyApp.AppImage</code></li>
<li>FUSE must be installed. Try: <code>sudo apt install fuse</code> or equivalent.</li>
<li>Check if it requires a specific library not present on your system.</li>
</ul>

<h3>AppImage extraction fails</h3>
<ul>
<li>Ensure you have write permission to the install directory (<code>~/Applications</code> by default).</li>
<li>Try running <code>niruvi --install PATH</code> for a retry with verbose output.</li>
<li>Check disk space: <code>df -h ~</code></li>
</ul>

<h3>GUI doesn't appear</h3>
<ul>
<li>Install PyQt6: <code>pip install PyQt6</code></li>
<li>On headless systems (no display), use CLI mode or forward your display (<code>export DISPLAY=:0</code>).</li>
<li>Check that the DISPLAY environment variable is set correctly.</li>
</ul>

<h3>Desktop entry not created</h3>
<ul>
<li>Check Settings: <b>create_desktop</b> must be <code>true</code>.</li>
<li>Verify <code>~/.local/share/applications/</code> exists and is writable.</li>
<li>Run <code>update-desktop-database ~/.local/share/applications/</code> to refresh.</li>
</ul>

<h3>Icon not showing in launcher</h3>
<ul>
<li>Ensure <b>icon_in_theme</b> is enabled in Settings.</li>
<li>Run <code>gtk-update-icon-cache</code> or log out and back in.</li>
<li>Some desktop environments cache icons aggressively; a reboot may help.</li>
</ul>

<h3>Build fails</h3>
<ul>
<li>Verify the source package is a valid DEB, RPM, or tar archive.</li>
<li>Check that required tools (<code>ar</code>, <code>rpm2cpio</code>, <code>cpio</code>, <code>tar</code>) are installed.</li>
<li>Ensure the output directory is writable and has enough free space.</li>
<li>Look at the build log in the dialog for specific error messages.</li>
</ul>

<h3>Reporting bugs</h3>
<p>Use <b>Help &rarr; Report Issue</b> to open the GitHub issues page and submit a bug report
or feature request. Before reporting:</p>
<ol>
<li>Check the error dialog's Technical Details tab for diagnostic information.</li>
<li>Click <b>Copy Report</b> in the error dialog to capture system info and logs.</li>
<li>Include the copied report in your GitHub issue for faster debugging.</li>
</ol>"""

    @staticmethod
    def _page_license():
        text = _GPL3_TEXT
        appdir = os.environ.get("APPDIR", "")
        candidates = [
            Path(__file__).resolve().parent.parent / "LICENSE",
            Path(__file__).resolve().parent.parent.parent / "LICENSE",
            Path(__file__).resolve().parent / "LICENSE",
            Path(os.getcwd()) / "LICENSE",
            Path.home() / ".local" / "share" / "Niruvi" / "LICENSE",
            Path("/usr/share") / "Niruvi" / "LICENSE",
        ]
        if appdir:
            candidates.append(Path(appdir) / "LICENSE")
        for p in candidates:
            if p.exists():
                text = p.read_text()
                break
        import html
        escaped = html.escape(text)
        return f"<h2>License (GPL-3.0)</h2><pre style='font-size:9pt;'>{escaped}</pre>"
