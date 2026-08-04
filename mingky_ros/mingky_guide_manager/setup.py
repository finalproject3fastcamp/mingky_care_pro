from setuptools import find_packages, setup

package_name = 'mingky_guide_manager'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', [f'resource/{package_name}']),
        (f'share/{package_name}', ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Mingky Care Team',
    maintainer_email='mingky-care@example.com',
    description='Patient guidance manager package for the Mingky Care project.',
    license='Apache-2.0',
    entry_points={'console_scripts': []},
)
