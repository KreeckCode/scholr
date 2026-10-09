"""Per-profile branding. SITE_PROFILE in .env selects one of these."""
import os
SITE_BRANDS = {

    'snap': {
        'profile':          'snap',
        'name':             'SNAP Uplift',
        'owner':            'Judea Harvest',
        'tagline':          'Training Platform',
        'strapline':        'Training for the pastors of Judea Harvest SNAP churches',
        'description':      'Practical, affordable pastoral development for church leaders '
                            'across Africa. Study at your own pace and pay only when you '
                            'claim your certificate.',
        'keywords':         'pastoral training Africa, church leadership training, SNAP churches, '
                            'Judea Harvest, ministry training, African pastors',

        'logo':             'logo/logo.webp',
        'logo_alt':         'Judea Harvest',
        'logo_needs_plate': True,
        'og_image':         'logo/og-snap.png',
        'theme_color':      '#0E1B33',

        'email':            'info@judeaharvest.co.za',
        'phone':            '+27 (0)79 831 7646',
        'phone_href':       '+27798317646',
        'address':          ['42 on Ingersol, Suite 6',
                             'Ingersol Road, Lynnwood Glen',
                             'Pretoria, 0081'],
        'website':          'https://www.judeaharvest.co.za',
        'website_label':    'judeaharvest.co.za',
        'twitter':          '@judeaharvest',

        'schema_type':      'EducationalOrganization',
        'founded':          '1999',
        'area_served':      'Africa',
        'knows_about':      ['Preaching and teaching', 'Pastoral care',
                             'Church leadership', 'Outreach and missions'],
        'same_as':          ['https://www.judeaharvest.co.za'],
        'locale':           'en_ZA',

        'footer_blurb':     'Mobilising, equipping and resourcing ministry leaders across '
                            'Africa since 1999, so that their communities may experience '
                            'the fullness of Jesus Christ.',
        'footer_column':    'The ministry',
        'footer_links':     [
            {'label': 'Tent evangelism',        'url': 'https://www.judeaharvest.co.za', 'external': True},
            {'label': 'SNAP church planting',   'url': 'https://www.judeaharvest.co.za', 'external': True},
            {'label': 'Hope Community Centres', 'url': 'https://www.judeaharvest.co.za', 'external': True},
            {'label': 'Support the work',       'url': 'https://www.judeaharvest.co.za', 'external': True},
        ],
        'footer_note':      '292 SNAP churches planted since 2017, in partnership with '
                            'International Cooperating Ministries.',
        'copyright':        'Judea Harvest',
        'copyright_note':   'Equipping African leaders since 1999.',

        'home_template':    'main/home_snap.html',
    },


    'metp': {
        'profile':          'metp',
        'name':             'METP',
        'owner':            'Ministry Essentials Training Program',
        'tagline':          'Ministry Essentials Training Program',
        'strapline':        'Equipping Christian ministry leaders everywhere',
        'description':      'Study the Ministry Essentials Training Program online, or launch a '
                            'licensed METP training location for your church, denomination or college.',
        'keywords':         'METP, Ministry Essentials Training Program, GlobalEd Institute, '
                            'Summit Pacific College, pastoral training, non-formal ministry training, '
                            'ministry certificate, Re-Forma',

        'logo':             'logo/metp.png',
        'logo_alt':         'Ministry Essentials Training Program',
        'logo_needs_plate': True,
        'og_image':         'logo/metp.png',
        'theme_color':      '#0E1B33',

        'email':            'metp.info@paoc.org',
        'phone':            '',
        'phone_href':       '',
        'address':          [],
        'website':          'https://metp.ca',
        'website_label':    'metp.ca',
        'twitter':          '',

        'schema_type':      'EducationalOrganization',
        'founded':          '2019',
        'area_served':      'Worldwide',
        'knows_about':      ['Theology and spirituality', 'Biblical interpretation',
                             'Pastoral care', 'Preaching', 'Evangelism and discipleship',
                             'Church leadership', 'Mentorship'],
        'same_as':          ['https://metp.ca', 'https://www.globaledinstitute.ca'],
        'locale':           'en_CA',

        'footer_blurb':     'A non-formal ministry training program of the GlobalEd Institute at '
                            'Summit Pacific College, equipping leaders with little or no access to '
                            'formal theological training.',
        'footer_column':    'METP',
        'footer_links':     [
            {'label': 'About METP',        'url': 'https://metp.ca/about/', 'external': True},
            {'label': 'Updates',           'url': 'https://metp.ca/blog/', 'external': True},
            {'label': 'Course overview',   'url': 'https://metp.ca/wp-content/uploads/2025/03/METP-Course-Overview-2025-03-02.pdf', 'external': True},
            {'label': 'Donate',            'url': 'https://paoc.org/donate/ministryessentialstrainingprogram-metp', 'external': True},
        ],
        'footer_note':      'Part of the GlobalEd department of Mission Global and the '
                            'Pentecostal Assemblies of Canada.',
        'copyright':        'GlobalEd Institute',
        'copyright_note':   '',

        'home_template':    'main/home_metp.html',
    },

    

    'school': {
        'profile':          'school',
        'name':             os.getenv('SCHOOL_NAME', 'Scholr'),
        'owner':            os.getenv('SCHOOL_NAME', 'Scholr'),
        'tagline':          'School Platform',
        'strapline':        'Admissions, academics and the parent portal',
        'description':      os.getenv('SCHOOL_DESCRIPTION',
                                    'Apply for a place, track your application, and '
                                    'manage your child\'s enrolment online.'),
        'keywords':         os.getenv('SCHOOL_KEYWORDS',
                                    'school admissions, CAPS curriculum, parent portal'),

        'logo':             os.getenv('SCHOOL_LOGO', 'logo/school.webp'),
        'logo_alt':         os.getenv('SCHOOL_NAME', 'Scholr'),
        'logo_needs_plate': os.getenv('SCHOOL_LOGO_PLATE', 'False').lower() == 'true',
        'og_image':         os.getenv('SCHOOL_OG_IMAGE', 'logo/og-school.png'),
        'theme_color':      os.getenv('SCHOOL_THEME_COLOR', '#0E1B33'),

        'email':            os.getenv('SCHOOL_EMAIL', ''),
        'phone':            os.getenv('SCHOOL_PHONE', ''),
        'phone_href':       os.getenv('SCHOOL_PHONE_HREF', ''),
        'address':          [p.strip() for p in os.getenv('SCHOOL_ADDRESS', '').split('|') if p.strip()],
        'website':          os.getenv('SCHOOL_WEBSITE', ''),
        'website_label':    os.getenv('SCHOOL_WEBSITE_LABEL', ''),
        'twitter':          os.getenv('SCHOOL_TWITTER', ''),

        'schema_type':      'School',
        'founded':          os.getenv('SCHOOL_FOUNDED', ''),
        'area_served':      os.getenv('SCHOOL_AREA', 'South Africa'),
        'knows_about':      [p.strip() for p in os.getenv(
                                'SCHOOL_KNOWS_ABOUT',
                                'CAPS curriculum|Primary education|Secondary education'
                            ).split('|') if p.strip()],
        'same_as':          [p.strip() for p in os.getenv('SCHOOL_SOCIAL', '').split('|') if p.strip()],
        'locale':           'en_ZA',

        'footer_blurb':     os.getenv('SCHOOL_BLURB', ''),
        'footer_column':    'The school',
        'footer_links':     [
            {'label': 'Admissions',     'url_name': 'enrollment:landing'},
            {'label': 'Check a status', 'url_name': 'enrollment:status'},
            {'label': 'Contact us',     'url_name': 'main:contact'},
        ],
        'footer_note':      os.getenv('SCHOOL_FOOTER_NOTE', ''),
        'copyright':        os.getenv('SCHOOL_NAME', 'Scholr'),
        'copyright_note':   '',

        'home_template':    'main/home_school.html',
    },
}