r"""
2D advection diffusion equation for tracers.

The advection-diffusion equation of tracer :math:`T` in conservative form reads

.. math::
    \frac{\partial T}{\partial t}
    + \nabla_h \cdot (\textbf{u} T)
    = \nabla_h \cdot (\mu_h \nabla_h T)
    :label: tracer_eq

where :math:`\nabla_h` denotes horizontal gradient, :math:`\textbf{u}` are the horizontal
velocities, respectively, and
:math:`\mu_h` denote horizontal and vertical diffusivity.
"""
from __future__ import absolute_import
from .utility import *
from .equation import Term, Equation

__all__ = [
    'TracerEquation',
    'TracerTerm',
    'HorizontalAdvectionTerm',
    'HorizontalDiffusionTerm',
    'SourceTerm',
]


class TracerTerm(Term):
    """
    Generic tracer term that provides commonly used members and mapping for
    boundary functions.
    """
    def __init__(self, function_space,
                 bathymetry=None, h_elem_size=None, use_lax_friedrichs=True):   
        """
        :arg function_space: :class:`FunctionSpace` where the solution belongs
        :kwarg bathymetry: bathymetry of the domain
        :type bathymetry: 3D :class:`Function` or :class:`Constant`
        :kwarg h_elem_size: scalar :class:`Function` that defines the horizontal
            element size
        """
        super(TracerTerm, self).__init__(function_space)
        self.bathymetry = bathymetry
        self.h_elem_size = h_elem_size
        continuity = element_continuity(self.function_space.ufl_element())
        self.horizontal_dg = continuity.horizontal == 'dg'
        self.use_lax_friedrichs = use_lax_friedrichs

        # define measures with a reasonable quadrature degree
        p, q = self.function_space.ufl_element().degree()
        self.quad_degree = (2*p + 1, 2*q + 1)
        self.dx = dx(degree=self.quad_degree)
        self.dS = dS(degree=self.quad_degree)
        self.ds = ds(degree=self.quad_degree)

    def get_bnd_functions(self, c_in, uv_in, elev_in, bnd_id, bnd_conditions):
        """
        Returns external values of tracer and uv for all supported
        boundary conditions.

        Volume flux (flux) and normal velocity (un) are defined positive out of
        the domain.

        :arg c_in: Internal value of tracer
        :arg uv_in: Internal value of horizontal velocity
        :arg elev_in: Internal value of elevation
        :arg bnd_id: boundary id
        :type bnd_id: int
        :arg bnd_conditions: dict of boundary conditions:
            ``{bnd_id: {field: value, ...}, ...}``
        """
        funcs = bnd_conditions.get(bnd_id)

        if 'elev' in funcs:
            elev_ext = funcs['elev']
        else:
            elev_ext = elev_in
        if 'value' in funcs:
            c_ext = funcs['value']
        else:
            c_ext = c_in
        if 'uv' in funcs:
            uv_ext = funcs['uv']
        elif 'flux' in funcs:
            assert self.bathymetry is not None
            h_ext = elev_ext + self.bathymetry
            area = h_ext*self.boundary_len  # NOTE using external data only
            uv_ext = funcs['flux']/area*self.normal
        elif 'un' in funcs:
            uv_ext = funcs['un']*self.normal
        else:
            uv_ext = uv_in

        return c_ext, uv_ext, elev_ext


class HorizontalAdvectionTerm(TracerTerm):
    # TODO adapt equation for 2-D case
    r"""
    Horizontal advection term :math:`\nabla_h \cdot (\textbf{u} T)`

    The weak formulation reads

    .. math::
        \int_\Omega \nabla_h \cdot (\textbf{u} T) \phi dx
            = -\int_\Omega T\textbf{u} \cdot \nabla_h \phi dx
            + \int_{\mathcal{I}_h\cup\mathcal{I}_v}
                T^{\text{up}} \text{avg}(\textbf{u}) \cdot
                \text{jump}(\phi \textbf{n}_h) dS

    where the right hand side has been integrated by parts;
    :math:`\mathcal{I}_h,\mathcal{I}_v` denote the set of horizontal and
    vertical facets,
    :math:`\textbf{n}_h` is the horizontal projection of the unit normal vector,
    :math:`T^{\text{up}}` is the upwind value, and :math:`\text{jump}` and
    :math:`\text{avg}` denote the jump and average operators across the
    interface.
    """
    def residual(self, solution, solution_old, fields, fields_old, bnd_conditions=None):
        if fields_old.get('uv_3d') is None:
            return 0
        elev = fields_old['elev_3d']
        uv = fields_old['uv_3d']
        uv_depth_av = fields_old['uv_depth_av']
        if uv_depth_av is not None:
            uv = uv + uv_depth_av

        uv_p1 = fields_old.get('uv_p1')
        uv_mag = fields_old.get('uv_mag')
        # FIXME is this an option?
        lax_friedrichs_factor = fields_old.get('lax_friedrichs_tracer_scaling_factor')

        f = 0
        f += -solution*inner(uv, nabla_grad(self.test))*self.dx
        if self.horizontal_dg:
            # add interface term
            uv_av = avg(uv)
            un_av = (uv_av[0]*self.normal('-')[0] +
                     uv_av[1]*self.normal('-')[1])
            s = 0.5*(sign(un_av) + 1.0)
            c_up = solution('-')*s + solution('+')*(1-s)
            f += c_up*(uv_av[0]*jump(self.test, self.normal[0]) +
                       uv_av[1]*jump(self.test, self.normal[1])) * self.dS 
           # Lax-Friedrichs stabilization
            if self.use_lax_friedrichs:
                if uv_p1 is not None:
                    gamma = 0.5*abs((avg(uv_p1)[0]*self.normal('-')[0] +
                                     avg(uv_p1)[1]*self.normal('-')[1]))*lax_friedrichs_factor
                elif uv_mag is not None:
                    gamma = 0.5*avg(uv_mag)*lax_friedrichs_factor
                else:
                    raise Exception('either uv_p1 or uv_mag must be given')
                f += gamma*dot(jump(self.test), jump(solution))*(self.dS) 
            if bnd_conditions is not None:
                for bnd_marker in self.boundary_markers:
                    funcs = bnd_conditions.get(bnd_marker)
                    ds_bnd = ds_v(int(bnd_marker), degree=self.quad_degree)    # FIXME Where does that ds_v term come from?
                    if funcs is None:
                        continue
                    else:
                        c_in = solution
                        c_ext, uv_ext, eta_ext = self.get_bnd_functions(c_in, uv, elev, bnd_marker, bnd_conditions)
                        uv_av = 0.5*(uv + uv_ext)
                        un_av = self.normal[0]*uv_av[0] + self.normal[1]*uv_av[1]
                        s = 0.5*(sign(un_av) + 1.0)
                        c_up = c_in*s + c_ext*(1-s)
                        f += c_up*(uv_av[0]*self.normal[0] +
                                   uv_av[1]*self.normal[1])*self.test*ds_bnd

        if self.use_symmetric_surf_bnd:
            f += solution*(uv[0]*self.normal[0] + uv[1]*self.normal[1])*self.test*ds_surf
        return -f


class HorizontalDiffusionTerm(TracerTerm):
    # TODO adapt equation for 2-D case
    r"""
    Horizontal diffusion term :math:`-\nabla_h \cdot (\mu_h \nabla_h T)`

    Using the symmetric interior penalty method the weak form becomes

    .. math::
        -\int_\Omega \nabla_h \cdot (\mu_h \nabla_h T) \phi dx
        =& \int_\Omega \mu_h (\nabla_h \phi) \cdot (\nabla_h T) dx \\
        &- \int_{\mathcal{I}_h\cup\mathcal{I}_v} \text{jump}(\phi \textbf{n}_h)
        \cdot \text{avg}(\mu_h \nabla_h T) dS
        - \int_{\mathcal{I}_h\cup\mathcal{I}_v} \text{jump}(T \textbf{n}_h)
        \cdot \text{avg}(\mu_h  \nabla \phi) dS \\
        &+ \int_{\mathcal{I}_h\cup\mathcal{I}_v} \sigma \text{avg}(\mu_h) \text{jump}(T \textbf{n}_h) \cdot
            \text{jump}(\phi \textbf{n}_h) dS

    where :math:`\sigma` is a penalty parameter,
    see Epshteyn and Riviere (2007).

    Epshteyn and Riviere (2007). Estimation of penalty parameters for symmetric
    interior penalty Galerkin methods. Journal of Computational and Applied
    Mathematics, 206(2):843-872. http://dx.doi.org/10.1016/j.cam.2006.08.029

    .. note ::
        Note the minus sign due to :class:`.equation.Term` sign convention
    """
    def residual(self, solution, solution_old, fields, fields_old, bnd_conditions=None):
        if fields_old.get('diffusivity_h') is None:
            return 0
        diffusivity_h = fields_old['diffusivity_h']
        diff_tensor = as_matrix([[diffusivity_h, 0, 0],
                                 [0, diffusivity_h, 0],
                                 [0, 0, 0]])
        grad_test = grad(self.test)
        diff_flux = dot(diff_tensor, grad(solution))

        f = 0
        f += inner(grad_test, diff_flux)*self.dx

        if self.horizontal_dg:
            assert self.h_elem_size is not None, 'h_elem_size must be defined'
          #  assert self.v_elem_size is not None, 'v_elem_size must be defined'
           
            # Interior Penalty method by
            # Epshteyn (2007) doi:10.1016/j.cam.2006.08.029
            # sigma = 3*k_max**2/k_min*p*(p+1)*cot(Theta)
            # k_max/k_min  - max/min diffusivity
            # p            - polynomial degree
            # Theta        - min angle of triangles
            # assuming k_max/k_min=2, Theta=pi/3
            # sigma = 6.93 = 3.5*p*(p+1)

       # TODO check if this is needed in 2-D :

            degree_h, degree_v = self.function_space.ufl_element().degree()
            # TODO compute elemsize as CellVolume/FacetArea
            elemsize = (self.h_elem_size*(self.normal[0]**2 +
                                          self.normal[1]**2)) # +
            sigma = 5.0*degree_h*(degree_h + 1)/elemsize
            if degree_h == 0:
                sigma = 1.5/elemsize
            alpha = avg(sigma)
            ds_interior = (self.dS) # _h + self.dS_v)
            f += alpha*inner(jump(self.test, self.normal),
                             dot(avg(diff_tensor), jump(solution, self.normal)))*ds_interior
            f += -inner(avg(dot(diff_tensor, grad(self.test))),
                        jump(solution, self.normal))*ds_interior
            f += -inner(jump(self.test, self.normal),
                        avg(dot(diff_tensor, grad(solution))))*ds_interior

        return -f

class SourceTerm(TracerTerm):
    """
    Generic source term

    The weak form reads

    .. math::
        F_s = \int_\Omega \sigma \phi dx

    where :math:`\sigma` is a user defined scalar :class:`Function`.

    .. note ::
        Due to the sign convention of :class:`.equation.Term`, this term is assembled to the left hand side of the equation
    """
    def residual(self, solution, solution_old, fields, fields_old, bnd_conditions=None):
        f = 0
        source = fields_old.get('source')
        if source is not None:
            f += -inner(source, self.test)*self.dx
        return -f


class TracerEquation(Equation):
    """
    3D tracer advection-diffusion equation :eq:`tracer_eq` in conservative form
    """
    def __init__(self, function_space,
                 bathymetry=None, h_elem_size=None,
                 use_lax_friedrichs=True):
        """
        :arg function_space: :class:`FunctionSpace` where the solution belongs
        :kwarg bathymetry: bathymetry of the domain
        :type bathymetry: 3D :class:`Function` or :class:`Constant`
        :kwarg h_elem_size: scalar :class:`Function` that defines the horizontal
            element size
        :kwarg bool use_symmetric_surf_bnd: If True, use symmetric surface boundary
            condition in the horizontal advection term
        """
        super(TracerEquation, self).__init__(function_space)

        args = (function_space, bathymetry,
                h_elem_size,  use_lax_friedrichs)
        self.add_term(HorizontalAdvectionTerm(*args), 'explicit')
        self.add_term(HorizontalDiffusionTerm(*args), 'explicit')
        self.add_term(SourceTerm(*args), 'source')
